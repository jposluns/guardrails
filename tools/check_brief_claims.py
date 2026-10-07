#!/usr/bin/env python3
"""Brief-claims lint: a dispatch brief's restatements of named review files must match those files.
Offline, stdlib only, fail-closed.

A brief often restates what a named review file says: its verdict, a finding's id or grade, a count. When
that restatement is carried over from text written about ANOTHER file (the template the brief was built
from, the previous round, the other reviewer), the brief asserts what a file contains without having read
it (read-before-characterizing, rdbchr) and hands a worker an unsound input (guard-input-soundness,
grdinp). This lint binds each restatement to the file it describes and compares it with that file.

The lint is ADOPTER-CONFIGURED: it ships NO family, file name, verdict or grade of its own. The adopter
authors `.aiqt/brief-claims.toml`. With NO configuration the scan prints NOT APPLICABLE and exits 0; a
present but unreadable or malformed configuration is cannot-evaluate (exit 2), never a clean pass.

Configuration schema (`.aiqt/brief-claims.toml`), every key REQUIRED:

  format-version = 1                  the exact integer 1 (true and 1.0 do not pass)
  families = ["alpha", "beta"]        reviewer family words (unique, case-insensitive)
  review-file-pattern = '...'         a regex (at most 512 characters) FULL-matched against a file's
                                      basename, with the named groups item, round (digits) and family
  verdicts = ["NO BLOCKERS", ...]     the exact verdict tokens (case-sensitive)
  grades = ["BLOCKER", "MAJOR", ...]  finding grade words (case-insensitive)
  grade-labels = ["Grade"]            label words of the "Label: GRADE" finding form
  verbatim-begin = "BEGIN VERBATIM"   a line starting with this opens an embedded verbatim review; the
                                      first family word after it is the section's declared family
  verbatim-end = "END VERBATIM"       a line starting with this closes it
  regrade-token = "REGRADED"          a claim segment carrying it is exempt from the verdict check (logged)
  [aliases.<family>]                  per-family shorthand: M = "MAJOR" lets "M3" mean that family's MAJOR 3
                                      (the table may be empty; an undeclared alias is cannot-evaluate)

Optional registry (`--registry PATH`, TOML, every key required): format-version = 1, target = "ITEM-7",
aliases = [...], dependencies = [...], id-pattern = '...' (the item id shape, at most 512 characters).
With it, an item id in the brief's own text that is not the target, a declared alias or a declared
dependency is a MISMATCH.

  check_brief_claims.py                                    validate the configuration ONLY; this is the
                                                           shipped CI form and it examines NO brief
  check_brief_claims.py --input-dir DIR [--template PATH] [--registry PATH] [--strict] BRIEF...
  check_brief_claims.py --self-test                        deterministic self-test (tempdir layer)

WHAT IT CHECKS. The brief's own text is every line outside fenced blocks, `>` lines and declared verbatim
sections. Both sides are compared in ONE normalized form: every default-ignorable or invisible character
(a BOM, a zero-width space, a variation selector, a combining grapheme joiner, a control character) is
removed, every whitespace or blank-rendering character (a no-break space, a thin space, a Hangul filler,
the blank Braille pattern) is a space, and claims and sources are NFKC-normalized (a file name is read
without NFKC, as it is spelled on disk). Quoted
strings are the brief's own text and are checked; to quote other text, use a fence, a `>` line or a
verbatim section. A fence follows CommonMark: at most three spaces of indentation, three or more
backticks or tildes, and no backtick in a backtick fence's info string (a line of inline code opens
nothing); it closes on a line of at least as many of the same character. An unclosed fence in a brief or
template is cannot-evaluate; in a named file it makes every claim bound to that file cannot-evaluate.

A named file is a token (split at whitespace, brackets, ASCII and typographic quotes, and , ; | * `)
whose basename full-matches the review-file pattern, or an absolute path under --input-dir; each must
resolve inside --input-dir to a readable UTF-8 regular file (a similarly named sibling is never
substituted). Binding: a balanced parenthetical directly after a named file is split at family words.
The text before the first family word, and a segment of that file's own family and round, bind to that
file itself; another family's segment binds to the named file of that family, item and round. "from
round N" rebinds a segment to round N, with or without a family word (without one, to the same family);
a round longer than nine digits, or two different rounds, is cannot-evaluate. A family word followed by
claims elsewhere in the brief's own text binds to
the named file of that family. Named tokens that resolve to one path are one candidate; two or more
candidates is ambiguous, never a choice.

Verdicts: ONE function classifies a verdict string on both sides. Each configured token matches at word
boundaries, a space inside a token matching any run of whitespace; an occurrence lying inside a longer
token's occurrence belongs to the longer token; occurrences of two tokens that partly overlap (NO
BLOCKERS FOUND under NO BLOCKERS and BLOCKERS FOUND) are ambiguous. A named file's verdict is its single
operative verdict record: a line that starts at column 0 with `VERDICT:` and whose value holds exactly one
token. An empty value, a value with no token or several, and a second record (even an identical one) are
cannot-evaluate. The line between a competing record and prose is a configured token: any other line that
holds the word verdict (any case or decoration) AND a configured token is a competing record, so the file
is cannot-evaluate (`Final VERDICT: X`, `1. VERDICT: X`, `VERDICT (revised): X`, `**Verdict:** X`, `VERDICT -
X`, a table cell, an indented or BOM-prefixed record, `Previous verdict: X`); a verdict line with no
configured token (`**Verdict:** the change is sound`) is prose and is ignored. On the brief side, different
tokens joined only by "or" or "/" (an instruction: "end with VERDICT: X or VERDICT: Y") are options, not a
claim.

Other claims: finding ids (GRADE-n, GRADE n, ranges, declared aliases), "GRADE:" (at least one finding at
that grade), "n GRADEs" (the graded-finding count), and "N word" and "N of M word" predicate counts. For a
predicate count the source is split into clauses at , ; : . ! ? before whitespace and at line ends; the
predicate word reads "N of M word" when the three words before it are exactly that, and otherwise pairs
with the one number among the three words before it in its clause. Numbers keep their sign (- + U+2212);
the supported number grammar is ASCII digits, optionally grouped by commas in threes, at most 18 digits,
and any other number form on either side (a decimal, a malformed group, the sign U+00B1, another script) is
cannot-evaluate. Two numbers in the window, a number that is itself the M of "N of M", the predicate
paired differently in different clauses, and a bare count that equals one number of an "N of M" reading
are ambiguous; a predicate never paired is not compared. A claim segment that is a verbatim excerpt of its
bound file's operative text stands for its ids, grades and counts, and an excerpt of a different named
file only is a misattribution; a verdict token in either is still judged against the verdict record, so
no excerpt passes a contradictory verdict. Embedded verbatim sections are attributed to
the named file holding a majority of their long lines (VERDICT lines do not vote), whose family and
verdict must equal the section's. With --template, a parenthetical identical to the template's but
attached to a different file, and an identical title naming a round the named inputs have moved past, are
mismatches.

BOUNDS. The two adopter regexes (review-file-pattern, id-pattern) are capped at 512 characters and are
matched only in a child interpreter (python -I -B) under a 20-second deadline per brief. The child runs
under a CPU-time limit (RLIMIT_CPU, 21 seconds) and, on Linux, dies with its parent (PR_SET_PDEATHSIG), so
it cannot outlive a killed gate; its address space is capped at 1 GiB where the platform allows. An
overrun, a failed child, a child that writes anything to stderr, and a platform with no RLIMIT_CPU are
cannot-evaluate (exit 2), so an adopter regex cannot hang the gate. Every other regex is built from
escaped configured words. A round, finding number or grade count longer than nine digits is never
converted (cannot-evaluate).

TIERS. Blocking: unresolved or unreadable inputs; verdicts, including a verdict claim with no resolvable
binding; misattributed excerpts; embedded attribution; template carry-over; foreign ids; and every
ambiguity (a binding with several candidate files, overlapping verdict tokens, a malformed or repeated
verdict record, an unclear predicate pairing). Advisory (printed WARN or CANNOT-EVALUATE, blocking only
under --strict): ids, grades and counts, an undeclared alias or an over-wide range, and an id or count
claim whose family has no named file.

WHAT IT DOES NOT PROVE (class c, partial). It sees only the claim shapes above, inside one line. Outside
it: a claim in other wording, including a verdict outside a parenthetical or family segment ("FILE: NO
BLOCKERS", "FILE says NO BLOCKERS"), a verdict token in another case or with lookalike letters (a Cyrillic
O, a letter with a combining mark); a round named in other wording ("round 4: NO BLOCKERS" without
"from"); a multi-line parenthetical; a binding through a file it cannot name; and a predicate count in a
form other than "N word" or "N of M word". A family word in an instruction that names ONE verdict token
("ask alpha to end with VERDICT: X") binds like a claim. A source line that uses the word verdict and a
configured token in another sense ("the verdict line must read X") makes that file cannot-evaluate.
Verdict classification is quadratic in the occurrences in one segment. An indented code block is the
brief's own text. Recall and the true-negative rate are not
measured. A MATCH says the restated token occurs in the bound file, not that the brief is right. The
configuration-only form, which is the shipped CI step, examines no brief: a brief is checked only when it
is named on the command line.

Exit convention (matches the repo's gates):
  0  clean, NOT APPLICABLE, or advisory results only (without --strict)
  1  a blocking MISMATCH (or, under --strict, an advisory WARN)
  2  cannot evaluate: a missing, unreadable, non-UTF-8, directory, or escaping named file; an unreadable
     brief, template or registry; a malformed configuration or registry; an adopter regex over its bound;
     a blocking claim the lint cannot evaluate (or, under --strict, any cannot-evaluate). An input it
     cannot read never reads as clean.
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
CONFIG_KEYS = frozenset(("format-version", "families", "review-file-pattern", "verdicts", "grades",
                         "grade-labels", "verbatim-begin", "verbatim-end", "regrade-token", "aliases"))
REGISTRY_KEYS = frozenset(("format-version", "target", "aliases", "dependencies", "id-pattern"))
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
# Default_Ignorable_Code_Point (Unicode DerivedCoreProperties): removed before any comparison.
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
REGEX_DEADLINE = 20.0  # seconds for one brief's adopter-regex matching in the child interpreter
CHILD_CPU = int(REGEX_DEADLINE) + 1  # the child's CPU-time limit in seconds (RLIMIT_CPU), past the deadline
CHILD_MEMORY = 1 << 30  # the child's address-space cap, where the platform allows one
MATCH, MISMATCH, CANNOT, WARN, EXEMPT = "MATCH", "MISMATCH", "CANNOT-EVALUATE", "WARN", "EXEMPT"
BLOCKING, ADVISORY = "blocking", "advisory"


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
        import signal
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
    for n, raw in enumerate(text.splitlines(), 1):
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


def _res(status, tier, lineno, claim, src, detail):
    return dict(status=status, tier=tier, lineno=lineno, claim=claim, src=src, detail=detail)


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
        return _res(EXEMPT, BLOCKING, lineno, claim, src,
                    "regrade disclosed ({}); verdict not compared".format(cfg["regrade"]))
    if kind == "verdict?":
        return _res(CANNOT, BLOCKING, lineno, claim, src, "overlapping verdict tokens: the claim is ambiguous")
    token, why = info["verdict"]
    if why is not None:
        return _res(CANNOT, BLOCKING, lineno, claim, src, "source: " + why)
    if token == claim:
        return _res(MATCH, BLOCKING, lineno, claim, src, "VERDICT: " + token)
    return _res(MISMATCH, BLOCKING, lineno, claim, src, "source VERDICT is {!r}".format(token))


def judge_segment(seg, files, cfg, infos):
    """Results for one claim segment. seg: lineno, text, token (bound file or None), why (if unbound),
    ambiguous (several candidate files)."""
    out = []
    n = seg["lineno"]
    claims = parse_claims(seg["text"], cfg)
    if seg["token"] is None:
        for kind, claim, _ in claims:
            hard = seg["ambiguous"] or kind in ("verdict", "verdict?")
            out.append(_res(CANNOT, BLOCKING if hard else ADVISORY, n, claim, None,
                            "no resolvable binding: " + seg["why"]))
        return out
    f, info = files[seg["token"]], infos[seg["token"]]
    src = (f["path"], f["sha"])
    excerpt = seg["text"].strip(" ,;:")
    long_excerpt = len(excerpt) >= MIN_EXCERPT
    if info["unclosed"] is not None:
        if claims or long_excerpt:
            out.append(_res(CANNOT, BLOCKING, n, excerpt, src, "a fence opened at source line {} is never "
                            "closed; the operative text cannot be read".format(info["unclosed"])))
        return out
    excerpt_only = False  # an excerpt stands for its ids, grades and counts, never for a verdict claim in it
    if long_excerpt:
        others = sorted(o["path"] for t, o in files.items() if t != seg["token"] and excerpt in infos[t]["text"])
        if excerpt in info["text"]:
            excerpt_only = True
            out.append(_res(MATCH, BLOCKING, n, excerpt, src, "verbatim excerpt of its bound file (a verdict "
                            "claim in it is judged on its own)"))
        elif others:
            excerpt_only = True
            out.append(_res(MISMATCH, BLOCKING, n, excerpt, src,
                            "misattributed: a verbatim excerpt of {}, not of its bound file".format(others[0])))
    fam = (f["groups"] or dict()).get("family")
    regrade = cfg["regrade"] in seg["text"]
    for kind, claim, m in claims:
        if kind in ("verdict", "verdict?"):
            out.append(_judge_verdict(kind, claim, info, cfg, n, src, regrade))
        elif excerpt_only:
            continue
        elif kind != "pred" and info["bad"] is not None:
            out.append(_res(CANNOT, ADVISORY, n, claim, src, info["bad"]))
        elif kind in ("id", "range", "alias"):
            try:
                wanted = _ids_of(kind, m, fam, cfg)
            except ValueError as exc:
                out.append(_res(CANNOT, ADVISORY, n, claim, src, str(exc)))
                continue
            for grade, num in wanted:
                label = "{}-{}".format(grade, num)
                ok = id_present(info, grade, num)
                out.append(_res(MATCH if ok else MISMATCH, ADVISORY, n, "id " + label, src,
                                "present" if ok else "no finding {} in the source".format(label)))
        elif kind == "colon":
            have = info["counts"].get(m.group(1).upper(), 0)
            out.append(_res(MATCH if have else MISMATCH, ADVISORY, n, claim, src,
                            "{} {} finding(s) in the source".format(have, m.group(1).upper())))
        elif kind == "count":
            grade, num = m.group(2).upper(), _bounded_int(m.group(1))
            if num is None:
                out.append(_res(CANNOT, ADVISORY, n, claim, src, "the count is longer than {} digits".format(
                    MAX_ROUND_DIGITS)))
                continue
            have = info["counts"].get(grade, 0)
            out.append(_res(MATCH if have == num else MISMATCH, ADVISORY, n, claim, src,
                            "the source grades {} finding(s) {}".format(have, grade)))
        else:
            verdict = predicate_check(m[0], m[1], info["text"])
            if verdict is not None:
                out.append(_res(verdict[0], BLOCKING if verdict[0] == CANNOT else ADVISORY, n, claim, src,
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
            out.append(_res(CANNOT, BLOCKING, n, claim, None, "no named file holds a majority of its long lines"))
            continue
        f = files[best[0]]
        src = (f["path"], f["sha"])
        sfam = (f["groups"] or dict()).get("family")
        if sec["family"] is None or sfam is None:
            out.append(_res(CANNOT, BLOCKING, n, claim, src, "family not determinable"))
        elif sfam != sec["family"]:
            out.append(_res(MISMATCH, BLOCKING, n, claim, src, "the source is family {!r}".format(sfam)))
        else:
            out.append(_res(MATCH, BLOCKING, n, claim, src, "family attribution"))
        lines, unclosed = operative_lines("\n".join(sec["lines"]))
        if verdict_records(lines, cfg):
            embedded, e_why = single_verdict(lines, unclosed, cfg)
            real, r_why = infos[best[0]]["verdict"]
            label = "embedded VERDICT: {}".format(embedded)
            if e_why is not None or r_why is not None:
                out.append(_res(CANNOT, BLOCKING, n, label, src, "embedded: {}; source: {}".format(
                    e_why or "one verdict", r_why or "one verdict")))
            elif real != embedded:
                out.append(_res(MISMATCH, BLOCKING, n, label, src, "source VERDICT is {!r}".format(real)))
            else:
                out.append(_res(MATCH, BLOCKING, n, label, src, "verdict"))
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
            out.append(_res(MISMATCH, BLOCKING, ref["lineno"], "({})".format(norm), (f["path"], f["sha"]),
                            "carried over: the template attaches this parenthetical to {}".format(moved[0])))
    n, title = _title(own)
    if title is not None and title == _title(t_own)[1]:
        m = ROUND_RE.search(title)
        rounds = set(r["groups"]["round"] for r in refs if r["groups"] and r["groups"]["round"] is not None)
        said = _bounded_int(m.group(1)) if m else None
        if m and rounds and said is None:
            out.append(_res(CANNOT, BLOCKING, n, title, None, "the title's round number is longer than {} "
                            "digits".format(MAX_ROUND_DIGITS)))
        elif m and rounds and said not in rounds and max(rounds) > said:
            out.append(_res(MISMATCH, BLOCKING, n, title, None,
                            "title carried over from the template names round {}; the named inputs are "
                            "round {}".format(m.group(1), max(rounds))))
    return out


def check_foreign(own, registry, found):
    """found: the registry id-pattern's matches per own line, answered by the bounded child."""
    out = []
    for (n, _), idents in zip(own, found):
        for ident in sorted(set(idents)):
            if ident not in registry["allowed"]:
                out.append(_res(MISMATCH, BLOCKING, n, ident, None,
                                "foreign item id: not the target, a declared alias or a declared dependency"))
    return out


def _shown(r):
    return WARN if r["status"] == MISMATCH and r["tier"] == ADVISORY else r["status"]


def _rc(results, strict):
    rc = 0
    for r in results:
        hard = r["tier"] == BLOCKING or strict
        if r["status"] == CANNOT and hard:
            rc = 2
        elif r["status"] == MISMATCH and hard:
            rc = max(rc, 1)
    return rc


def check_brief(path, cfg, opts, template_text, registry):
    text, digest = read_text(path, "brief")
    own, sections = brief_regions(text, cfg)
    t_own = brief_regions(template_text, cfg, "template")[0] if template_text is not None else []
    bases = sorted(set(tok.rsplit("/", 1)[-1] for _, _, tok in _tokens(own + t_own)))
    jobs = [(cfg["pattern"].pattern, "fullmatch", bases)]
    if registry is not None:
        jobs.append((registry["id_re"].pattern, "findall", [line for _, line in own]))
    answers = bounded_regex(jobs)
    groups_of = dict((b, _groups(found)) for b, found in zip(bases, answers[0]) if found is not None)
    refs = named_refs(own, groups_of, opts["input_dir"])
    files, errors = dict(), []
    for ref in refs:
        if ref["token"] in files:
            continue
        try:
            p, real, t, d = resolve_named(ref["token"], opts["input_dir"])
        except GateError as exc:
            errors.append("brief line {}: {}".format(ref["lineno"], exc))
            continue
        files[ref["token"]] = dict(path=p, real=real, text=t, sha=d, groups=ref["groups"])
    print("BRIEF {} sha256={}".format(path, digest))
    if errors:
        for e in errors:
            print("CANNOT-EVALUATE [blocking] {}; fail-closed".format(e))
        print("brief-claims: {}: {} named file(s) could not be read; exit 2".format(path, len(errors)))
        return 2
    for token in sorted(files):
        print("INPUT {} sha256={}".format(files[token]["path"], files[token]["sha"]))
    infos = dict((t, analyze_source(f["text"], cfg)) for t, f in files.items())
    segs, parens = collect_segments(own, refs, cfg, files)
    results = []
    for seg in segs:
        results.extend(judge_segment(seg, files, cfg, infos))
    results.extend(check_sections(sections, files, cfg, infos))
    if template_text is not None:
        results.extend(check_template(own, refs, parens, files, t_own, named_refs(t_own, groups_of, None)))
    if registry is not None:
        results.extend(check_foreign(own, registry, answers[1]))
    else:
        print("foreign-id: not evaluated (no --registry)")
    tally = dict()
    for r in results:
        src = "{} sha256={}".format(*r["src"]) if r["src"] else "(no source)"
        print("{} [{}] brief line {}: {} -> {}: {}".format(_shown(r), r["tier"], r["lineno"], r["claim"], src,
                                                           r["detail"]))
        tally[_shown(r)] = tally.get(_shown(r), 0) + 1
    rc = _rc(results, opts["strict"])
    print("brief-claims: {}: {} result(s) {}; exit {}".format(
        path, len(results), ", ".join("{} {}".format(v, k) for k, v in sorted(tally.items())) or "none", rc))
    return rc


def run(root, opts):
    """Check the named briefs under the adopter configuration at root. Returns the exit code 0/1/2."""
    config_path = Path(root) / CONFIG_REL
    try:
        try:
            st = os.lstat(config_path)
        except FileNotFoundError:
            print("NOT APPLICABLE: no {} configuration; the brief-claims lint is adopter-configured and "
                  "inert without one ({} brief(s) named, none checked)".format(CONFIG_REL, len(opts["briefs"])))
            return 0
        except OSError as exc:
            raise GateError("configuration {} cannot be examined ({})".format(CONFIG_REL, exc.strerror or exc))
        if stat.S_ISLNK(st.st_mode):
            raise GateError("configuration {} is a symlink; it is not followed".format(CONFIG_REL))
        cfg = load_config(config_path)
        if not opts["briefs"]:
            print("PASS: configuration {} is valid; no brief named, so NO brief was examined (this "
                  "configuration-only form is the shipped CI step)".format(CONFIG_REL))
            return 0
        if opts["input_dir"] is None or not os.path.isdir(opts["input_dir"]):
            raise GateError("--input-dir must name an existing directory when a brief is named")
        template_text = read_text(opts["template"], "template")[0] if opts["template"] else None
        registry = load_registry(opts["registry"]) if opts["registry"] else None
        rc = 0
        for brief in opts["briefs"]:
            rc = max(rc, check_brief(brief, cfg, opts, template_text, registry))
        return rc
    except GateError as exc:
        print("CANNOT-EVALUATE: {}; fail-closed".format(exc))
        return 2


def parse_args(argv):
    opts = dict(self_test=False, strict=False, input_dir=None, template=None, registry=None, briefs=[])
    it = iter(argv)
    for arg in it:
        if arg == "--self-test":
            opts["self_test"] = True
        elif arg == "--strict":
            opts["strict"] = True
        elif arg in ("--input-dir", "--template", "--registry"):
            value = next(it, None)
            if value is None:
                return None
            opts[arg[2:].replace("-", "_")] = value
        elif arg.startswith("-"):
            return None
        else:
            opts["briefs"].append(arg)
    return opts



# --- self-test --------------------------------------------------------------------------------------
# Every vector runs the real run() over a synthetic tempdir tree. Placeholder families, items and files
# only. No randomness, no network; the clocks are the regex child's deadline (two catastrophic-
# backtracking vectors shorten it to two seconds), its CPU limit (one vector lowers it to one second), and
# the Linux parent-death probe (a copy of this gate in the tempdir, killed once its child exists).

GOOD_CFG = """format-version = 1
families = ["alpha", "beta"]
review-file-pattern = '(?P<item>[a-z0-9]+)-r(?P<round>[0-9]+)-(?P<family>[a-z]+)\\.txt'
verdicts = ["NO BLOCKERS", "BLOCKERS FOUND"]
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


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data.encode("utf-8") if isinstance(data, str) else data)


def _quiet(root, argv):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        rc = run(root, parse_args(argv))
    return rc, buf.getvalue()


def _scenario(base, name, brief, files=None, cfg=GOOD_CFG, args=(), extra=None):
    """Build base/name with a configuration, an input dir and a brief; run; return (rc, output)."""
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
    brief_path = os.path.join(root, "brief.md")
    if brief is not None:
        _write(brief_path, brief.replace("@IN@", indir))
    argv = [a.replace("@ROOT@", root) for a in args]
    return _quiet(root, ["--input-dir", indir] + argv + [brief_path])



def _cases_inputs(sc, check, skipped, base):
    """Plan step 2: configuration, inputs and exit codes."""
    os.makedirs(os.path.join(base, "nocfg"))
    rc, out = _quiet(os.path.join(base, "nocfg"), [])
    check("cfg/absent-not-applicable", rc == 0 and "NOT APPLICABLE" in out, out)
    bad = [("cfg/version-float", GOOD_CFG.replace("format-version = 1", "format-version = 1.0")),
           ("cfg/version-bool", GOOD_CFG.replace("format-version = 1", "format-version = true")),
           ("cfg/missing-key", GOOD_CFG.replace('regrade-token = "REGRADED"\n', "")),
           ("cfg/duplicate-family", GOOD_CFG.replace('"alpha", "beta"]', '"alpha", "beta", "Alpha"]')),
           ("cfg/empty-vocabulary", GOOD_CFG.replace('["NO BLOCKERS", "BLOCKERS FOUND"]', "[]")),
           ("cfg/pattern-groups", GOOD_CFG.replace("(?P<family>[a-z]+)", "([a-z]+)")),
           ("cfg/malformed-toml", GOOD_CFG + "[[[\n"),
           ("cfg/alias-undeclared-grade", GOOD_CFG.replace('M = "MAJOR"', 'M = "SEVERE"')),
           ("cfg/alias-undeclared-family", GOOD_CFG.replace("[aliases.beta]", "[aliases.gamma]")),
           ("cfg/pattern-too-long", GOOD_CFG.replace("(?P<item>[a-z0-9]+)", "(?P<item>[a-z0-9]+)" + "(?:x)?" * 90))]
    for name, text in bad:
        rc, out = sc(name.replace("/", "_"), "Nothing named.\n", cfg=text)
        check(name, rc == 2, out)
    rc, out = sc("cfg_valid", "Nothing named here.\n")
    check("cfg/valid-no-claims", rc == 0, out)
    root = os.path.join(base, "cfglink")
    _write(os.path.join(root, "real.toml"), GOOD_CFG)
    os.makedirs(os.path.join(root, ".aiqt"))
    os.symlink(os.path.join(root, "real.toml"), os.path.join(root, CONFIG_REL))
    check("cfg/symlink", _quiet(root, [])[0] == 2)
    root = os.path.join(base, "noindir")
    _write(os.path.join(root, CONFIG_REL), GOOD_CFG)
    _write(os.path.join(root, "b.md"), "x\n")
    check("in/missing-input-dir", _quiet(root, [os.path.join(root, "b.md")])[0] == 2)
    rc, out = sc("abs_missing", "Read @IN@/notes/summary.md first.\n")
    check("in/missing-absolute-path", rc == 2 and "does not exist" in out, out)
    opened = []
    me = sys.modules[__name__]
    real_read = me._read_bytes
    me._read_bytes = lambda p: opened.append(os.path.basename(p)) or real_read(p)
    try:
        rc, out = sc("failed_sibling", "Inputs: it7-r5-alpha.txt (NO BLOCKERS).\n",
                     files=dict([("it7-r5-alpha-FAILED.txt", ALPHA5)]))
    finally:
        me._read_bytes = real_read
    check("in/failed-sibling-not-substituted", rc == 2 and "it7-r5-alpha-FAILED.txt" not in opened, out)
    if os.geteuid() != 0:
        root = os.path.join(base, "mode000")
        p = os.path.join(root, "in")
        _write(os.path.join(p, "it7-r5-alpha.txt"), ALPHA5)
        _write(os.path.join(root, CONFIG_REL), GOOD_CFG)
        _write(os.path.join(root, "b.md"), "Inputs: it7-r5-alpha.txt.\n")
        os.chmod(os.path.join(p, "it7-r5-alpha.txt"), 0)
        try:
            rc0 = _quiet(root, ["--input-dir", p, os.path.join(root, "b.md")])[0]
        finally:
            os.chmod(os.path.join(p, "it7-r5-alpha.txt"), 0o644)
        check("in/mode-000", rc0 == 2)
    else:
        skipped.append("in/mode-000 (running as root, permissions do not bind)")
    rc, out = sc("non_utf8", "Inputs: it7-r5-alpha.txt.\n", files=dict([("it7-r5-alpha.txt", b"\xff\xfe x")]))
    check("in/non-utf8", rc == 2, out)
    rc, out = sc("directory", "Inputs: it7-r5-alpha.txt.\n", files=dict([("it7-r5-alpha.txt/x", "x")]))
    check("in/directory", rc == 2 and "not a regular file" in out, out)
    outside = os.path.join(base, "outside.txt")
    _write(outside, ALPHA5)
    root = os.path.join(base, "symesc")
    indir = os.path.join(root, "in")
    os.makedirs(indir)
    os.symlink(outside, os.path.join(indir, "it7-r5-alpha.txt"))
    _write(os.path.join(root, CONFIG_REL), GOOD_CFG)
    _write(os.path.join(root, "b.md"), "Inputs: it7-r5-alpha.txt (BLOCKERS FOUND).\n")
    check("in/symlink-escape", _quiet(root, ["--input-dir", indir, os.path.join(root, "b.md")])[0] == 2)
    rc, out = sc("no_brief", None)
    check("in/unreadable-brief", rc == 2, out)
    rc, out = sc("unterminated", "BEGIN VERBATIM alpha\nline\n")
    check("in/unterminated-verbatim", rc == 2, out)



def _cases_claims(sc, check, base):
    """Plan steps 3 to 6: binding, verdicts, ids and grades, counts."""
    rc, out = sc("bind_list", "Inputs: it7-r5-alpha.txt, it7-r5-beta.txt (alpha MEDIUM-1; beta NO BLOCKERS)\n",
                 args=["--strict"])
    check("bind/list-family-segment", rc == 0 and "id MEDIUM-1 -> " + os.path.join(
        base, "bind_list", "in", "it7-r5-alpha.txt") in out, out)
    r9 = dict([("it7-r9-alpha.txt", ALPHA5), ("it7-r8-beta.txt", BETA5.replace("### MINOR n2", "### MINOR 7"))])
    brief = "Inputs: it7-r9-alpha.txt (alpha MAJOR-2, beta MINOR-7 from round 8)\n"
    rc, out = sc("bind_round_unnamed", brief, files=r9)
    check("bind/from-round-unnamed", rc == 0 and "CANNOT-EVALUATE [advisory]" in out and "round 8" in out, out)
    rc, out = sc("bind_round_unnamed_strict", brief, files=r9, args=["--strict"])
    check("bind/from-round-unnamed-strict", rc == 2, out)
    rc, out = sc("bind_round_named", "Inputs: it7-r8-beta.txt and " + brief[8:], files=r9, args=["--strict"])
    check("bind/from-round-named", rc == 0 and "MATCH [advisory] brief line 1: id MINOR-7" in out, out)
    hidden = [("bind/fence-not-extracted", "```\nit7-r5-alpha.txt (NO BLOCKERS)\n```\nit7-r5-alpha.txt\n"),
              ("bind/quote-line-not-extracted", "> it7-r5-alpha.txt (NO BLOCKERS)\nit7-r5-alpha.txt\n"),
              ("bind/verbatim-not-extracted", "it7-r5-alpha.txt\nBEGIN VERBATIM alpha\nalpha NO BLOCKERS\n"
               "2. **Major** - the retry loop never backs off between attempts on a busy server.\nEND VERBATIM\n")]
    for name, text in hidden:
        rc, out = sc(name.replace("/", "_").replace("-", "_"), text, args=["--strict"])
        check(name, rc == 0, out)
    rc, out = sc("nested", "Inputs: it7-r5-alpha.txt (see (the log) for MAJOR-3 and BLOCKERS FOUND)\n")
    check("bind/nested-parens", rc == 0 and "MATCH [advisory] brief line 1: id MAJOR-3" in out
          and "MATCH [blocking] brief line 1: BLOCKERS FOUND" in out, out)
    rc, out = sc("prose_family", "Fix the alpha MEDIUM-1 finding; it7-r5-alpha.txt is the source.\n",
                 args=["--strict"])
    check("bind/prose-family-claim", rc == 0 and "MATCH [advisory] brief line 1: id MEDIUM-1" in out, out)
    rc, out = sc("prose_ambiguous", "Fix alpha MAJOR-2 now. Inputs: it7-r5-alpha.txt, it7-r4-alpha.txt.\n",
                 files=dict([("it7-r5-alpha.txt", ALPHA5), ("it7-r4-alpha.txt", ALPHA5)]))
    check("bind/prose-family-ambiguous", rc == 2 and "CANNOT-EVALUATE [blocking]" in out and "ambiguous" in out, out)

    rc, out = sc("v_mismatch", "Inputs: it7-r5-alpha.txt (NO BLOCKERS)\n")
    check("verdict/mismatch", rc == 1 and "MISMATCH [blocking]" in out, out)
    rc, out = sc("v_match", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n")
    check("verdict/match", rc == 0 and "MATCH [blocking]" in out, out)
    rc, out = sc("v_none", "Inputs: it7-r5-alpha.txt (NO BLOCKERS)\n",
                 files=dict([("it7-r5-alpha.txt", ALPHA5.replace("VERDICT: BLOCKERS FOUND\n", ""))]))
    check("verdict/none", rc == 2, out)
    fenced = "```\nVERDICT: BLOCKERS FOUND\n```\n> VERDICT: BLOCKERS FOUND\n" + BETA5
    rc, out = sc("v_fenced", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n", files=dict([("it7-r5-beta.txt", fenced)]))
    check("verdict/fenced-and-quoted-ignored", rc == 0, out)
    rc, out = sc("src_quoted_id", "Inputs: it7-r5-alpha.txt (MAJOR 9)\n",
                 files=dict([("it7-r5-alpha.txt", "> MAJOR 9 was quoted from elsewhere.\n" + ALPHA5)]))
    check("src/quoted-id-ignored", "WARN [advisory] brief line 1: id MAJOR-9" in out, out)
    rc, out = sc("v_two", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n",
                 files=dict([("it7-r5-beta.txt", BETA5 + "VERDICT: BLOCKERS FOUND\n")]))
    check("verdict/two-distinct", rc == 2, out)
    rc, out = sc("v_severity", "Inputs: it7-r5-alpha.txt (no BLOCKER-grade finding remains)\n", args=["--strict"])
    check("verdict/severity-wording-not-a-claim", rc == 0 and "result(s) none" in out, out)
    rc, out = sc("v_regrade", "Inputs: it7-r5-alpha.txt (NO BLOCKERS, REGRADED after fix)\n")
    check("verdict/regrade-exempt-logged", rc == 0 and "EXEMPT [blocking]" in out, out)


    blob = BETA5.replace("### MEDIUM 1\n", "### NOTE\naGVsbG8M1d29ybGQ=\n")
    rc, out = sc("id_base64", "Inputs: it7-r5-beta.txt (beta M1)\n", files=dict([("it7-r5-beta.txt", blob)]))
    check("id/base64-substring-absent", "WARN [advisory] brief line 1: id MEDIUM-1" in out, out)
    rc, out = sc("id_whole", "Inputs: it7-r5-beta.txt (MEDIUM-1)\n",
                 files=dict([("it7-r5-beta.txt", "Notes on MEDIUM-12 only.\nVERDICT: NO BLOCKERS\n")]))
    check("id/whole-token", "WARN [advisory] brief line 1: id MEDIUM-1" in out, out)
    rc, out = sc("id_alias", "Inputs: it7-r5-alpha.txt, it7-r5-beta.txt (alpha M3; beta M1)\n", args=["--strict"])
    check("id/alias-per-family", rc == 0 and "id MAJOR-3" in out and "id MEDIUM-1" in out, out)
    rc, out = sc("id_alias_undeclared", "Inputs: it7-r5-alpha.txt (alpha X2)\n")
    check("id/undeclared-alias", rc == 0 and "CANNOT-EVALUATE [advisory]" in out and "not declared" in out, out)
    rc, out = sc("id_minor_n", "Inputs: it7-r5-beta.txt (2 MINOR, MINOR-2)\n", args=["--strict"])
    check("id/minor-n-headings", rc == 0 and "MATCH [advisory] brief line 1: 2 MINOR" in out, out)
    rc, out = sc("id_section", "Inputs: it7-r5-alpha.txt (see MEDIUM 16.1)\n", args=["--strict"])
    check("id/section-number-not-id", rc == 0 and "result(s) none" in out, out)
    rc, out = sc("id_absent", "Inputs: it7-r5-alpha.txt (MAJOR 9)\n")
    rc2 = sc("id_absent_strict", "Inputs: it7-r5-alpha.txt (MAJOR 9)\n", args=["--strict"])[0]
    check("id/absent-injected", rc == 0 and rc2 == 1 and "WARN [advisory]" in out, out)
    rc, out = sc("id_wrong_grade", "Inputs: it7-r5-alpha.txt (alpha blocker 2)\n")
    check("id/wrong-grade-item", "WARN [advisory] brief line 1: id BLOCKER-2" in out, out)
    rc, out = sc("id_colon", "Inputs: it7-r5-beta.txt (beta MAJOR: the timeout)\n")
    check("id/grade-colon-absent", "WARN [advisory] brief line 1: MAJOR:" in out, out)
    rc, out = sc("id_range", "Inputs: it7-r5-beta.txt (MINOR-1 to MINOR-3)\n")
    check("id/range-expands", "MATCH [advisory] brief line 1: id MINOR-2" in out
          and "WARN [advisory] brief line 1: id MINOR-3" in out, out)
    rc, out = sc("id_alias_range", "Inputs: it7-r5-alpha.txt (alpha M2-M4)\n")
    check("id/alias-range-expands", "MATCH [advisory] brief line 1: id MAJOR-3" in out
          and "WARN [advisory] brief line 1: id MAJOR-4" in out, out)

    seven = dict([("it7-r5-alpha.txt", "1. **Blocker** - a.\n2. **Blocker** - b.\n3. **Major** - c.\n"
                   "4. **Major** - d.\n5. **Major** - e.\n6. **Major** - f.\n7. **Major** - g.\n"
                   "VERDICT: BLOCKERS FOUND\n")])
    rc, out = sc("count_warn", "Inputs: it7-r5-alpha.txt (2 blockers, 4 majors)\n", files=seven)
    rc2 = sc("count_warn_strict", "Inputs: it7-r5-alpha.txt (2 blockers, 4 majors)\n", files=seven,
             args=["--strict"])[0]
    check("count/grade-count-warn", rc == 0 and rc2 == 1 and "WARN [advisory] brief line 1: 4 majors" in out
          and "MATCH [advisory] brief line 1: 2 blockers" in out, out)
    pred = dict([("it7-r5-beta.txt", "Across 16,000 seeded mutations, 2,642 were accepted by the grammar.\n"
                  "VERDICT: NO BLOCKERS\n")])
    rc, out = sc("count_pred", "Inputs: it7-r5-beta.txt (16,000 accepted mutations)\n", files=pred)
    check("count/predicate-mismatch", "WARN [advisory] brief line 1: 16,000 accepted" in out, out)
    rc, out = sc("count_pred_ok", "Inputs: it7-r5-beta.txt (2,642 were accepted)\n", files=pred, args=["--strict"])
    check("count/predicate-match", rc == 0 and "MATCH [advisory]" in out, out)
    forms = dict([("it7-r5-beta.txt", "Item one. Grade: MAJOR\nItem two. Defect: MAJOR\n"
                   "| Grade: MAJOR | table |\nVERDICT: NO BLOCKERS\n")])
    rc, out = sc("count_forms", "Inputs: it7-r5-beta.txt (2 MAJORs)\n", files=forms, args=["--strict"])
    check("count/label-forms-counted-table-skipped", rc == 0 and "MATCH [advisory] brief line 1: 2 MAJORs" in out,
          out)
    rc, out = sc("count_round", "Inputs: it7-r5-beta.txt (the round-7 minors stay open)\n", args=["--strict"])
    check("count/round-prefix-not-count", rc == 0 and "result(s) none" in out, out)



def _cases_template(sc, check):
    """Plan steps 7 to 9: template carry-over, foreign ids, embedded attribution, excerpts, report."""
    tmpl = dict([("tmpl.md", "# Fix item seven round 4\n"
                  "Inputs: it7-r4-alpha.txt (alpha MEDIUM-1 and MAJOR-2 remain open)\n")])
    targ = ["--template", "@ROOT@/tmpl.md"]
    rc, out = sc("t_reattach", "# Fix item seven round 5\n"
                 "Inputs: it7-r5-alpha.txt (alpha MEDIUM-1 and MAJOR-2 remain open)\n", extra=tmpl, args=targ)
    check("tmpl/parenthetical-reattached", rc == 1 and "carried over" in out, out)
    rc, out = sc("t_title", "# Fix item seven round 4\nInputs: it7-r5-alpha.txt.\n", extra=tmpl, args=targ)
    check("tmpl/title-round-carried", rc == 1 and "title carried over" in out, out)
    rc, out = sc("t_clean", "# Fix item seven round 5\nInputs: it7-r5-alpha.txt (alpha MEDIUM-1).\n",
                 extra=tmpl, args=targ)
    check("tmpl/clean", rc == 0, out)
    reg = dict([("reg.toml", 'format-version = 1\ntarget = "ITEM-7"\naliases = ["ITEM-7B"]\n'
                 'dependencies = ["ITEM-2"]\nid-pattern = \'ITEM-[0-9]+[A-Z]?\'\n')])
    rarg = ["--registry", "@ROOT@/reg.toml"]
    rc, out = sc("f_foreign", "TASK: finish ITEM-6 using it7-r5-alpha.txt.\n", extra=reg, args=rarg)
    check("tmpl/foreign-id", rc == 1 and "foreign item id" in out, out)
    rc, out = sc("f_clean", "TASK: finish ITEM-7 (alias ITEM-7B) after ITEM-2.\n> history: ITEM-6 was done\n",
                 extra=reg, args=rarg)
    check("tmpl/foreign-id-quoted-dependency-alias-clean", rc == 0, out)
    rc, out = sc("f_none", "TASK: finish ITEM-6.\n")
    check("tmpl/no-registry-not-evaluated", rc == 0 and "foreign-id: not evaluated" in out, out)
    rc, out = sc("t_unreadable", "x\n", args=["--template", "@ROOT@/absent.md"])
    check("tmpl/unreadable-template", rc == 2, out)
    rc, out = sc("r_unreadable", "x\n", args=["--registry", "@ROOT@/absent.toml"])
    check("tmpl/unreadable-registry", rc == 2, out)
    rc, out = sc("r_malformed", "x\n", extra=dict([("reg.toml", 'format-version = 1\ntarget = "ITEM-7"\n')]),
                 args=rarg)
    check("tmpl/malformed-registry", rc == 2, out)


    section = ("2. **Major** - the retry loop never backs off between attempts on a busy server.\n"
               "3. **Major** - the cache key ignores the locale and returns stale rows.\n")
    rc, out = sc("e_swapped", "Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM beta\n" + section + "END VERBATIM\n")
    check("emb/swapped-family", rc == 1 and "the source is family 'alpha'" in out, out)
    rc, out = sc("e_verdict", "Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM alpha\n" + section
                 + "VERDICT: NO BLOCKERS\nEND VERBATIM\n")
    check("emb/verdict-differs", rc == 1 and "MISMATCH [blocking]" in out, out)
    rc, out = sc("e_ok", "Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM alpha\n" + section
                 + "VERDICT: BLOCKERS FOUND\nEND VERBATIM\n", args=["--strict"])
    check("emb/attribution-match", rc == 0 and "MATCH [blocking]" in out, out)
    rc, out = sc("e_nosource", "Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM alpha\n"
                 "This sentence appears in no named review file at all, anywhere.\nEND VERBATIM\n")
    check("emb/no-source", rc == 2, out)
    rc, out = sc("x_wrong", "Inputs: it7-r5-alpha.txt, it7-r5-beta.txt (the cache key ignores the locale and returns stale rows)\n")
    check("emb/excerpt-wrong-file", rc == 1 and "misattributed" in out, out)
    rc, out = sc("x_own", "Inputs: it7-r5-alpha.txt (the retry loop never backs off between attempts)\n",
                 args=["--strict"])
    check("emb/excerpt-own-file-passes", rc == 0 and "verbatim excerpt of its bound file" in out, out)

    rc, out = sc("report", "Inputs: it7-r5-alpha.txt, it7-r5-beta.txt (beta NO BLOCKERS)\n")
    digests = [hashlib.sha256(d.encode()).hexdigest() for d in (ALPHA5, BETA5)]
    check("report/every-sha256", rc == 0 and all("sha256=" + d in out for d in digests), out)


def _cases_round2(sc, check, base):
    """Binding ambiguity, predicate pairing, regex bounds, verdict records, fences, names and quotes."""
    two = dict([("a/it7-r5-alpha.txt", "VERDICT: NO BLOCKERS\n"), ("b/it7-r5-alpha.txt", "VERDICT: BLOCKERS FOUND\n")])
    rc, out = sc("b2_adjacent", "a/it7-r5-alpha.txt\nb/it7-r5-alpha.txt (alpha NO BLOCKERS)\n", files=two,
                 args=["--strict"])
    check("bind/adjacent-family-binds-itself", rc == 1 and "b/it7-r5-alpha.txt sha256=" in out, out)
    betas = dict([("it7-r5-alpha.txt", ALPHA5), ("a/it7-r5-beta.txt", BETA5), ("b/it7-r5-beta.txt", BETA5)])
    rc, out = sc("b2_other_amb", "Inputs: a/it7-r5-beta.txt, b/it7-r5-beta.txt, it7-r5-alpha.txt (beta MEDIUM-1)\n",
                 files=betas)
    check("bind/other-family-ambiguous", rc == 2 and "ambiguous between" in out, out)
    rc, out = sc("b2_same_file", "Fix the alpha MEDIUM-1 finding; it7-r5-alpha.txt and @IN@/it7-r5-alpha.txt "
                 "are the source.\n", args=["--strict"])
    check("bind/one-path-one-candidate", rc == 0 and "MATCH [advisory] brief line 1: id MEDIUM-1" in out, out)
    r45 = dict([("it7-r5-alpha.txt", ALPHA5), ("it7-r4-alpha.txt", ALPHA5)])
    rc, out = sc("b2_prose_verdict", "Inputs: it7-r5-alpha.txt, it7-r4-alpha.txt. The alpha verdict: NO BLOCKERS.\n",
                 files=r45)
    check("bind/prose-verdict-ambiguous", rc == 2, out)
    rc, out = sc("b2_unnamed_verdict", "Inputs: it7-r5-beta.txt (alpha NO BLOCKERS)\n")
    check("bind/unnamed-family-verdict", rc == 2 and "none named" in out, out)
    rc, out = sc("b2_unbalanced", "Inputs: it7-r5-alpha.txt (NO BLOCKERS\n")
    check("bind/unbalanced-paren-verdict", rc == 2 and "unbalanced parenthetical" in out, out)
    r98 = dict([("it7-r9-alpha.txt", ALPHA5), ("it7-r8-alpha.txt", ALPHA5)])
    rc, out = sc("b2_mask", "Inputs: it7-r9-alpha.txt, it7-r8-alpha.txt (alpha MAJOR-2 from round 8)\n", files=r98,
                 args=["--strict"])
    check("bind/parenthetical-masked-from-prose", rc == 0 and "MATCH [advisory] brief line 1: id MAJOR-2" in out, out)

    def pred(name, source, claim, args=()):
        return sc(name, "Inputs: it7-r5-beta.txt ({})\n".format(claim),
                  files=dict([("it7-r5-beta.txt", source + "VERDICT: NO BLOCKERS\n")]), args=list(args))
    rc, out = pred("p2_cross", "20 seeded, 10 accepted.\n", "20 accepted", ["--strict"])
    check("pred/no-crossing-mismatch", rc == 1 and "binds 'accepted' to 10" in out, out)
    rc, out = pred("p2_right", "20 seeded, 10 accepted.\n", "10 accepted", ["--strict"])
    check("pred/no-crossing-match", rc == 0 and "MATCH [advisory] brief line 1: 10 accepted" in out, out)
    rc, out = pred("p2_window", "10 of 20 accepted.\n", "10 accepted")
    check("pred/two-numbers-ambiguous", rc == 2 and "more than one number" in out, out)
    rc, out = pred("p2_runon", "20 seeded 10 accepted\n", "20 accepted")
    check("pred/run-on-ambiguous", rc == 2, out)
    rc, out = pred("p2_clauses", "10 accepted in round one; 12 accepted in round two.\n", "10 accepted")
    check("pred/clauses-disagree", rc == 2 and "different numbers" in out, out)

    me = sys.modules[__name__]
    saved = me.REGEX_DEADLINE
    me.REGEX_DEADLINE = 2.0
    try:
        slow = GOOD_CFG.replace("(?P<item>[a-z0-9]+)", "(?P<item>(a+)+)")
        rc, out = sc("r2_pattern", "Inputs: " + "a" * 40 + "x\n", cfg=slow)
        check("regex/review-file-pattern-deadline", rc == 2 and "deadline" in out, out)
        reg = dict([("reg.toml", 'format-version = 1\ntarget = "ITEM-7"\naliases = []\ndependencies = []\n'
                     "id-pattern = '(a+)+b'\n")])
        rc, out = sc("r2_idpat", "TASK: " + "a" * 40 + "c\n", extra=reg, args=["--registry", "@ROOT@/reg.toml"])
        check("regex/id-pattern-deadline", rc == 2 and "deadline" in out, out)
    finally:
        me.REGEX_DEADLINE = saved
    real_run = me.subprocess.run

    def refuse(*args, **kwargs):
        raise ValueError("preexec_fn is not supported on this platform")
    me.subprocess.run = refuse
    try:
        rc, out = sc("r2_child", "Inputs: it7-r5-alpha.txt (NO BLOCKERS)\n")
    finally:
        me.subprocess.run = real_run
    check("regex/child-start-failure", rc == 2 and "could not run" in out, out)
    long_reg = dict([("reg.toml", 'format-version = 1\ntarget = "ITEM-7"\naliases = []\ndependencies = []\n'
                      "id-pattern = 'ITEM-[0-9]+" + "(?:x)?" * 90 + "'\n")])
    rc, out = sc("r2_idlong", "TASK: ITEM-7.\n", extra=long_reg, args=["--registry", "@ROOT@/reg.toml"])
    check("regex/id-pattern-too-long", rc == 2 and "longer than" in out, out)

    records = [("src-verdict/empty-second", BETA5 + "VERDICT:\n"),
               ("src-verdict/indented-second", BETA5 + " VERDICT: BLOCKERS FOUND\n"),
               ("src-verdict/bom-first", "\ufeffVERDICT: BLOCKERS FOUND\nVERDICT: NO BLOCKERS\n"),
               ("src-verdict/duplicate-identical", BETA5 + "VERDICT: NO BLOCKERS\n"),
               ("src-verdict/indented-only", BETA5.replace("VERDICT:", "  VERDICT:")),
               ("src-verdict/bom-only", "\ufeffVERDICT: NO BLOCKERS\n"),
               ("src-verdict/decorated", BETA5.replace("VERDICT: NO BLOCKERS", "**VERDICT:** NO BLOCKERS")),
               ("src-verdict/other-case", BETA5.replace("VERDICT:", "Verdict:")),
               ("src-verdict/no-token", BETA5.replace("VERDICT: NO BLOCKERS", "VERDICT: PASS")),
               ("src-verdict/empty-only", BETA5.replace("VERDICT: NO BLOCKERS", "VERDICT:")),
               ("src-verdict/overlap", BETA5.replace("VERDICT: NO BLOCKERS", "VERDICT: NO BLOCKERS FOUND")),
               ("fence/unclosed-source", "```\n" + BETA5)]
    for name, text in records:
        rc, out = sc(name.replace("/", "_").replace("-", "_"), "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n",
                     files=dict([("it7-r5-beta.txt", text)]))
        check(name, rc == 2, out)
    tail = BETA5.replace("VERDICT: NO BLOCKERS", "VERDICT: NO BLOCKERS (two minors deferred)")
    rc, out = sc("v2_trailing", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n", files=dict([("it7-r5-beta.txt", tail)]),
                 args=["--strict"])
    check("src-verdict/value-classified-by-shared-function", rc == 0 and "MATCH [blocking]" in out, out)
    rc, out = sc("v2_brief_overlap", "Inputs: it7-r5-alpha.txt (NO BLOCKERS FOUND)\n")
    check("verdict/brief-overlap-ambiguous", rc == 2 and "ambiguous" in out, out)
    rc, out = sc("v2_nbsp", "Inputs: it7-r5-alpha.txt (NO\u00a0BLOCKERS)\n")
    check("verdict/nbsp-inside-token", rc == 1, out)
    nested = GOOD_CFG.replace('["NO BLOCKERS", "BLOCKERS FOUND"]', '["NO BLOCKERS", "BLOCKERS FOUND", "BLOCKERS"]')
    rc, out = sc("v2_contained", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n", cfg=nested, args=["--strict"])
    check("verdict/contained-token-belongs-to-longer", rc == 0 and "MATCH [blocking]" in out, out)

    rc, out = sc("f2_inline", "```make test``` runs the suite.\nInputs: it7-r9-alpha.txt (NO BLOCKERS)\n")
    check("fence/inline-code-opens-nothing", rc == 2 and "does not exist" in out, out)
    rc, out = sc("f2_indented", "    ```\nInputs: it7-r9-alpha.txt (NO BLOCKERS)\n")
    check("fence/indented-opens-nothing", rc == 2 and "does not exist" in out, out)
    rc, out = sc("f2_unclosed", "```\nInputs: it7-r9-alpha.txt (NO BLOCKERS)\n")
    check("fence/unclosed-brief", rc == 2 and "never closed" in out, out)
    longer = "````\n```\nVERDICT: BLOCKERS FOUND\n````\n" + BETA5
    rc, out = sc("f2_length", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n", files=dict([("it7-r5-beta.txt", longer)]),
                 args=["--strict"])
    check("fence/closer-at-least-opener-length", rc == 0 and "MATCH [blocking]" in out, out)
    tilde = "~~~ a`b\nVERDICT: BLOCKERS FOUND\n~~~\n" + BETA5
    rc, out = sc("f2_tilde", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n", files=dict([("it7-r5-beta.txt", tilde)]),
                 args=["--strict"])
    check("fence/tilde-info-may-hold-backtick", rc == 0 and "MATCH [blocking]" in out, out)

    names = [("name/bom-brief", "\ufeffit7-r9-alpha.txt (NO BLOCKERS)\n"),
             ("name/zero-width-space", "Inputs: \u200bit7-r9-alpha.txt (NO BLOCKERS)\n"),
             ("name/typographic-possessive", "it7-r9-alpha.txt\u2019s verdict (NO BLOCKERS)\n"),
             ("name/curly-quoted", "Inputs: \u201cit7-r9-alpha.txt\u201d.\n"),
             ("name/huge-round", "Inputs: it7-r" + "9" * 5000 + "-alpha.txt.\n")]
    for name, text in names:
        rc, out = sc(name.replace("/", "_").replace("-", "_"), text)
        check(name, rc == 2 and "named file" in out, out)
    sup = GOOD_CFG.replace("(?P<round>[0-9]+)", "(?P<round>[0-9\\u00b2]+)")
    rc, out = sc("n2_superscript", "Inputs: it7-r\u00b2-alpha.txt (NO BLOCKERS)\n", cfg=sup,
                 files=dict([("it7-r\u00b2-alpha.txt", ALPHA5)]))
    check("name/non-decimal-round", rc == 1, out)

    quotes = [("quote/dquote-family-claim-checked", 'See it7-r5-alpha.txt "alpha NO BLOCKERS".\n'),
              ("quote/ascii-quoted-verdict-checked", 'Inputs: it7-r5-alpha.txt ("NO BLOCKERS")\n'),
              ("quote/curly-quoted-verdict-checked", "Inputs: it7-r5-alpha.txt (\u201cNO BLOCKERS\u201d)\n"),
              ("quote/stray-inch-mark", 'A 3" gap; it7-r5-alpha.txt (NO BLOCKERS); the "end".\n')]
    for name, text in quotes:
        rc, out = sc(name.replace("/", "_").replace("-", "_"), text, args=["--strict"])
        check(name, rc == 1 and "MISMATCH [blocking]" in out, out)

    rc, out = sc("m2_range", "Inputs: it7-r5-beta.txt (MINOR-1 to MINOR-90)\n")
    rc2 = sc("m2_range_strict", "Inputs: it7-r5-beta.txt (MINOR-1 to MINOR-90)\n", args=["--strict"])[0]
    check("id/range-too-wide", rc == 0 and rc2 == 2 and "cannot be expanded" in out and "id MINOR-90" not in out, out)
    rc, out = sc("m2_alias_range", "Inputs: it7-r5-alpha.txt (alpha M1-M90)\n")
    check("id/alias-range-too-wide", rc == 0 and "cannot be expanded" in out and "id MAJOR-90" not in out, out)
    section = ("Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM alpha\n"
               "3. **Major** - the cache key ignores the locale and returns stale rows.\n"
               "VERDICT: BLOCKERS FOUND (one blocker and two majors remain open)\nEND VERBATIM\n")
    rc, out = sc("m2_section", section, args=["--strict"])
    check("emb/verdict-line-does-not-vote", rc == 0 and "MATCH [blocking] brief line 2: embedded VERDICT" in out, out)
    hidden = "```\ntemplate says VERDICT: NO BLOCKERS\n```\nVERDICT: BLOCKERS FOUND\n"
    rc, out = sc("m2_excerpt", "Inputs: it7-r5-alpha.txt (template says VERDICT: NO BLOCKERS)\n",
                 files=dict([("it7-r5-alpha.txt", hidden)]))
    check("emb/excerpt-matches-operative-text-only", rc == 1 and "MISMATCH [blocking]" in out, out)
    rc, out = sc("f2_open_id", "Inputs: it7-r5-alpha.txt (MAJOR-2)\n", files=dict([("it7-r5-alpha.txt", "```\n" + ALPHA5)]))
    check("fence/unclosed-source-id-claim", rc == 2 and "never closed" in out, out)
    rc, out = sc("f2_open_emb", "Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM alpha\n"
                 "3. **Major** - the cache key ignores the locale and returns stale rows.\n"
                 "VERDICT: BLOCKERS FOUND\nEND VERBATIM\n", files=dict([("it7-r5-alpha.txt", ALPHA5 + "```\nopen\n")]))
    check("fence/unclosed-source-embedded", rc == 2 and "never closed" in out, out)
    rc, out = sc("b2_own_round", "Inputs: it7-r9-alpha.txt (alpha MAJOR-9 from round 8)\n",
                 files=dict([("it7-r9-alpha.txt", ALPHA5)]), args=["--strict"])
    check("bind/own-family-other-round-rebinds", rc == 2 and "none named" in out, out)
    root = os.path.join(base, "ci_form")
    _write(os.path.join(root, CONFIG_REL), GOOD_CFG)
    rc, out = _quiet(root, [])
    check("ci/configuration-only-examines-no-brief", rc == 0 and "NO brief was examined" in out, out)


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


def _cases_round3(sc, check, base, skipped):
    """QA round 2: excerpts never skip a verdict, verdict records in other forms, one normalization on both
    sides, signed and unsupported numbers, "N of M" pairing, round rebinding, bounded numbers, option lists,
    and the regex child's CPU limit, parent death, clean stderr and isolation."""
    def one(name, source, claim, args=("--strict",), fname="it7-r5-alpha.txt"):
        return sc(name, "Inputs: {} ({})\n".format(fname, claim), files=dict([(fname, source)]), args=list(args))

    rc, out = one("x3_judged", "Earlier text: NO BLOCKERS (review complete)\nVERDICT: BLOCKERS FOUND\n",
                  "NO BLOCKERS (review complete)", ())
    check("excerpt/verdict-still-judged", rc == 1 and "MISMATCH [blocking] brief line 1: NO BLOCKERS" in out, out)
    rc, out = one("x3_prev", "Previous verdict: NO BLOCKERS (review complete)\nVERDICT: BLOCKERS FOUND\n",
                  "NO BLOCKERS (review complete)", ())
    check("excerpt/competing-record", rc == 2, out)
    rc, out = one("x3_dup", "VERDICT: NO BLOCKERS (review complete)\nVERDICT: BLOCKERS FOUND\n",
                  "VERDICT: NO BLOCKERS (review complete)", ())
    check("excerpt/duplicate-record", rc == 2 and "2 operative verdict lines" in out, out)
    forms = [("numbered-before", "1. VERDICT: BLOCKERS FOUND\nVERDICT: NO BLOCKERS\n"),
             ("final", "VERDICT: NO BLOCKERS\nFinal VERDICT: BLOCKERS FOUND\n"),
             ("revised", "VERDICT: NO BLOCKERS\nVERDICT (revised): BLOCKERS FOUND\n"),
             ("numbered-after", "VERDICT: NO BLOCKERS\n1. VERDICT: BLOCKERS FOUND\n"),
             ("bold-final", "VERDICT: NO BLOCKERS\n**Final verdict:** BLOCKERS FOUND\n"),
             ("dash", "VERDICT: NO BLOCKERS\nVERDICT - BLOCKERS FOUND\n"),
             ("table", "VERDICT: NO BLOCKERS\n| VERDICT: | BLOCKERS FOUND |\n")]
    for name, source in forms:
        rc, out = one("v3_" + name.replace("-", "_"), source, "NO BLOCKERS")
        check("src-verdict/token-in-other-form-" + name, rc == 2 and "outside a column-0 VERDICT" in out, out)
    prose = [("bold", BETA5 + "**Verdict:** the change is sound; two minors remain.\n"),
             ("bullet", BETA5 + "- Verdict: approve after the two minors.\n")]
    for name, source in prose:
        rc, out = one("v3_prose_" + name, source, "NO BLOCKERS", fname="it7-r5-beta.txt")
        check("src-verdict/prose-without-token-" + name, rc == 0 and "MATCH [blocking]" in out, out)
    account = "Verdict: PATCH DELIVERED\nSweep: 41 of 42 mutants killed; one survives.\n"
    rc, out = one("v3_account", account, "PATCH DELIVERED; 41 of 42 mutants killed")
    check("src-verdict/account-form", rc == 0 and "MATCH [advisory] brief line 1: 41 of 42 mutants" in out, out)
    for cp in ("\u034f", "\ufe0f", "\u3164", "\u115f", "\u2800"):
        rc, out = one("n3_" + hex(ord(cp)), ALPHA5, "NO" + cp + " BLOCKERS", ())
        check("norm/invisible-U+{:04X}".format(ord(cp)), rc == 1 and "MISMATCH [blocking]" in out, out)
    for cp in ("\u3164", "\u2800"):
        rc, out = one("n3_glued_" + hex(ord(cp)), ALPHA5, "NO" + cp + "BLOCKERS", ())
        check("norm/blank-reads-as-space-U+{:04X}".format(ord(cp)), rc == 1, out)
    for cp in ("\u00a0", "\u2009"):
        rc, out = sc("n3_space_" + hex(ord(cp)), "Inputs: it7-r5-alpha.txt" + cp + "(NO BLOCKERS)\n")
        check("norm/space-before-parenthetical-U+{:04X}".format(ord(cp)), rc == 1, out)
    rc, out = one("n3_nfkc_brief", ALPHA5, "\uff2e\uff2f BLOCKERS", ())
    check("norm/nfkc-brief", rc == 1, out)
    wide = ALPHA5.replace("VERDICT: BLOCKERS", "VERDICT: \uff22\uff2c\uff2f\uff23\uff2b\uff25\uff32\uff33")
    rc, out = one("n3_nfkc_source", wide, "BLOCKERS FOUND")
    check("norm/nfkc-source", rc == 0 and "MATCH [blocking]" in out, out)
    section = ("Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM alpha\n"
               "3. **Major** - the cache key ignores the locale and\u00a0returns stale rows.\nEND VERBATIM\n")
    rc, out = sc("n3_section", section, args=["--strict"])
    check("norm/embedded-section", rc == 0 and "MATCH [blocking] brief line 2: embedded section" in out, out)

    tail = "VERDICT: NO BLOCKERS\n"
    rc, out = one("p3_sign", "-20 accepted.\n" + tail, "20 accepted")
    check("pred/sign-kept-source", rc == 1 and "to -20" in out, out)
    rc, out = one("p3_sign_both", "-20 accepted.\n" + tail, "\u221220 accepted")
    check("pred/sign-kept-both", rc == 0 and "MATCH [advisory]" in out, out)
    rc, out = one("p3_decimal_src", "20.5 accepted.\n" + tail, "20 accepted", ())
    check("pred/unsupported-source-number", rc == 2 and "outside the supported grammar" in out, out)
    rc, out = one("p3_decimal_brief", "20 accepted.\n" + tail, "20.5 accepted", ())
    check("pred/unsupported-brief-number", rc == 2 and "outside the supported grammar" in out, out)
    rc, out = one("p3_long", "20 accepted.\n" + tail, "9" * 30 + " accepted", ())
    check("pred/over-long-number", rc == 2 and "outside the supported grammar" in out, out)
    sweep = "Sweep: 41 of 42 mutants killed; one survives.\n" + tail
    for name, claim, want in (("restated", "41 of 42 mutants killed", 0), ("of-differs", "40 of 42 mutants", 1),
                              ("bare-differs", "43 mutants", 1), ("bare-total", "42 mutants", 2),
                              ("verb-after-noun", "41 killed", 2)):
        rc, out = one("p3_of_" + name.replace("-", "_"), sweep, claim)
        check("pred/n-of-m-" + name, rc == want, out)
    rc, out = one("x3_counts", "20 seeded 10 accepted by the grammar here.\n" + tail,
                  "20 seeded 10 accepted by the grammar here")
    check("excerpt/stands-for-counts", rc == 0 and "verbatim excerpt" in out, out)
    rc, out = sc("n3_nfkc_prose", "Inputs: it7-r5-alpha.txt. The alpha verdict: \uff2e\uff2f BLOCKERS.\n")
    check("norm/nfkc-prose-family-claim", rc == 1, out)
    rc, out = sc("n3_nfkc_template", "# Fix item seven round 5\n"
                 "Inputs: it7-r5-alpha.txt (alpha MEDIUM-1 and MAJOR-2 remain open)\n",
                 extra=dict([("tmpl.md", "# Fix item seven round 4\n"
                              "Inputs: it7-r4-alpha.txt (alpha \uff2dEDIUM-1 and MAJOR-2 remain open)\n")]),
                 args=["--template", "@ROOT@/tmpl.md"])
    check("norm/nfkc-template", rc == 1 and "carried over" in out, out)
    rc, out = one("n3_source_text", "The retry loop never backs off\u00a0between attempts.\n" + tail,
                  "The retry loop never backs off between attempts")
    check("norm/source-operative-text", rc == 0 and "verbatim excerpt" in out, out)
    rc, out = one("p3_one_digit", "4 accepted.\n" + tail, "3 accepted")
    check("pred/one-digit-not-a-claim", rc == 0 and "result(s) none" in out, out)
    rc, out = one("b3_huge_id", ALPHA5, "MAJOR-" + "9" * 5000, ())
    check("bound/finding-id", rc == 0 and "longer than" in out, out)
    rc, out = one("p3_line_end", "Count 30\n20 accepted by the grammar.\n" + tail, "20 accepted")
    check("pred/clause-ends-at-line-end", rc == 0 and "MATCH [advisory]" in out, out)
    rc, out = one("f3_same_char", "```\n~~~\nVERDICT: BLOCKERS FOUND\n```\n" + BETA5, "NO BLOCKERS")
    check("fence/closer-same-character", rc == 0 and "MATCH [blocking]" in out, out)
    r45 = dict([("it7-r4-alpha.txt", ALPHA5), ("it7-r5-alpha.txt", tail)])
    rc, out = sc("b3_round", "Inputs: it7-r4-alpha.txt, it7-r5-alpha.txt (from round 4: NO BLOCKERS)\n", files=r45)
    check("bind/round-qualified-segment-rebinds", rc == 1 and any(
        l.startswith("MISMATCH") and "it7-r4-alpha.txt sha256" in l for l in out.splitlines()), out)
    rc, out = sc("b3_round_same", "Inputs: it7-r4-alpha.txt, it7-r5-alpha.txt (from round 5: NO BLOCKERS)\n",
                 files=r45, args=["--strict"])
    check("bind/round-qualified-own-round", rc == 0 and "MATCH [blocking]" in out, out)
    rc, out = sc("b3_round_none", "Inputs: it7-r5-alpha.txt (from round 6: NO BLOCKERS)\n", files=r45)
    check("bind/round-qualified-unnamed", rc == 2 and "none named" in out, out)
    huge = "9" * 5000
    rc, out = sc("b3_huge", "Inputs: it7-r5-alpha.txt (alpha NO BLOCKERS from round " + huge + ")\n")
    check("bound/rebinding-round", rc == 2 and "longer than" in out, out)
    rc, out = sc("b3_huge_count", "Inputs: it7-r5-alpha.txt (" + huge + " MAJORs)\n")
    check("bound/grade-count", rc == 0 and "longer than" in out, out)
    rc, out = one("b3_huge_src", huge + ". **Major** - x.\nVERDICT: BLOCKERS FOUND\n", "MAJOR-2")
    check("bound/source-finding-number", rc == 2 and "too long to read" in out, out)
    rc, out = sc("b3_huge_title", "# Fix round " + huge + "\nInputs: it7-r5-alpha.txt.\n",
                 extra=dict([("tmpl.md", "# Fix round " + huge + "\n")]), args=["--template", "@ROOT@/tmpl.md"])
    check("bound/template-title-round", rc == 2 and "longer than" in out, out)

    rc, out = sc("o3_options", "alpha leg: review the diff, then end with VERDICT: BLOCKERS FOUND or VERDICT: NO "
                 "BLOCKERS\nInputs: it7-r5-alpha.txt\n", args=["--strict"])
    check("verdict/option-list-not-a-claim", rc == 0 and "result(s) none" in out, out)
    rc, out = sc("o3_not_options", "Inputs: it7-r5-alpha.txt (alpha NO BLOCKERS, not BLOCKERS FOUND)\n")
    check("verdict/two-tokens-not-options", rc == 1 and "MISMATCH [blocking]" in out, out)

    me = sys.modules[__name__]
    real_run = me.subprocess.run

    def noisy(*args, **kwargs):
        done = real_run(*args, **kwargs)
        return subprocess.CompletedProcess(done.args, done.returncode, done.stdout, b"Warning: noise\n")
    me.subprocess.run = noisy
    try:
        rc, out = sc("r3_stderr", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n")
    finally:
        me.subprocess.run = real_run
    check("regex/child-stderr-fails-closed", rc == 2 and "wrote to stderr" in out, out)
    planted = os.path.join(base, "planted")
    _write(os.path.join(planted, "hashlib.py"), "raise SystemExit(7)\n")
    saved_env = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = planted
    try:
        rc, out = sc("r3_isolated", "Inputs: it7-r5-alpha.txt (NO BLOCKERS)\n")
    finally:
        if saved_env is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = saved_env
    check("regex/child-isolated", rc == 1 and "MISMATCH [blocking]" in out, out)
    saved = (me.REGEX_DEADLINE, me.CHILD_CPU)
    me.REGEX_DEADLINE, me.CHILD_CPU = 60.0, 1
    try:
        slow = GOOD_CFG.replace("(?P<item>[a-z0-9]+)", "(?P<item>(a+)+)")
        rc, out = sc("r3_cpu", "Inputs: " + "a" * 40 + "x\n", cfg=slow)
    finally:
        me.REGEX_DEADLINE, me.CHILD_CPU = saved
    check("regex/child-cpu-limit", rc == 2 and "failed with exit" in out and "deadline" not in out, out)
    if sys.platform.startswith("linux"):
        ok, detail = _orphan_probe(base)
        check("regex/child-dies-with-parent", ok, detail)
    else:
        skipped.append("regex/child-dies-with-parent (Linux only)")


def self_test_main():
    import shutil
    import tempfile
    failures, skipped, ran = [], [], []

    def check(name, cond, out=""):
        ran.append(name)
        if not cond:
            failures.append((name, " | ".join(out.strip().splitlines()[-4:])))

    base = tempfile.mkdtemp(prefix="brief-claims-selftest-")
    try:
        def sc(name, brief, **kw):
            return _scenario(base, name, brief, **kw)
        _cases_inputs(sc, check, skipped, base)
        _cases_claims(sc, check, base)
        _cases_template(sc, check)
        _cases_round2(sc, check, base)
        _cases_round3(sc, check, base, skipped)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL: {} of {} vector(s)".format(len(failures), len(ran)))
        for name, out in failures:
            print("  - FAIL {}: {}".format(name, out))
        return 1
    if skipped:
        print("SELF-TEST PASS (PARTIAL): {} vector(s) held; SKIPPED (UNVERIFIED this run): {}".format(
            len(ran), "; ".join(skipped)))
    else:
        print("SELF-TEST PASS: {} vector(s) held (configuration and input fail-closed, binding, verdicts, ids "
              "and grades, counts, template carry-over, foreign ids, embedded attribution, excerpts, report "
              "digests, fences, verdict records, predicate pairing, regex bounds)".format(len(ran)))
    return 0


def main():
    if sys.argv[1:] == ["--regex-worker"]:
        return regex_worker()
    opts = parse_args(sys.argv[1:])
    if opts is None:
        print("usage: check_brief_claims.py [--self-test] [--strict] [--input-dir DIR] [--template PATH] "
              "[--registry PATH] [BRIEF...]; fail-closed", file=sys.stderr)
        return 2
    if opts["self_test"]:
        return self_test_main()
    return run(Path(__file__).resolve().parents[1], opts)


if __name__ == "__main__":
    sys.exit(main())
