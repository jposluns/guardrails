#!/usr/bin/env python3
"""orch_round_gate.py: the per-item QA round record of ENF-ROUND-PROGRESS-GATE and its validating reader.

This release ships the record format and the reader only. It makes no dispatch decision: there is no
counting, no permit and no stall answer yet, and nothing in the pack calls it.

  orch_round_gate.py validate --record ABS   exit 0 the record holds exactly the form below,
                                             exit 2 cannot evaluate (fail closed)
  any other argument list                    exit 2 (refused before anything is read)

The self-test is its own runner, tools/selftest_orch_round_gate.py (suite round-gate-selftest).

OUTPUT. stdout carries one JSON object (decision VALID or CANNOT_EVALUATE, with the item, line count, tip
seq, last round and last attempt on VALID, or the reason on CANNOT_EVALUATE), then one human-readable
line. On exit 2 stderr carries one line starting "CANNOT EVALUATE:". Every input the reader cannot
evaluate, an error the reader did not foresee included, gives that one CANNOT_EVALUATE object and exit 2:
main routes any unexpected exception through the same refusal. Every value a reason quotes (a record
value, a field name, an argument, a path, a path component or an exception's message) goes through one
formatter, _show (reprlib: a string's repr cut to about 80 characters, a list to 6 entries and an object
to 4, 3 levels deep), so no value can overflow the stack or swell a reason; and every reason, the usage
refusal and the unforeseen-error refusal included, is cut to at most MAX_REASON characters (the cut is
marked). A caller must treat any exit other than 0, a missing or unparseable stdout object, or a
decision that disagrees with the exit, as not valid. Everything the reader prints is printable ASCII, so
no value it reports (a path holding an LF, say) can add or split a line: the JSON object is written with JSON escapes and carries the reason
exactly, and every human-readable line, the fallback error line included, writes a backslash as two
backslashes and every other character outside printable ASCII as \\xNN, \\uNNNN or \\UNNNNNNNN.

Two cases fall outside that framing, and both exit 2 with no complete stdout object, which the rule above
reads as not valid. Under a Python older than 3.14 the pack's shared version guard (one template, held by
tools/check_python_floor.py) runs before anything else: it writes its refusal to stderr and names the
interpreter path as the platform gives it, unescaped, so that path can hold any character and span lines.
And if stdout itself cannot be written or flushed, the process entry writes one escaped stderr line.

THE RECORD. One file per item, runtime state, never tracked in the repository. Append-only JSONL: UTF-8
with no byte order mark, every line ended by LF (a final line without LF is truncated), no carriage return
anywhere, no blank line, at most MAX_LINE_BYTES per line and MAX_RECORD_BYTES per file. Each line is one
JSON object with no duplicate key, no NaN or Infinity, no value nested more than MAX_DEPTH levels deep
(the line's object is level one; the form below reaches level five), no field beyond the ones listed, and
no boolean or float where an integer is expected.

Chain. Every line carries seq (0 on the first line, then each line's seq is its line index, no gaps) and
prev (the lowercase sha256 hex of the previous line's raw bytes without its LF; 64 zeros on line 0).
Beside the record sits <record>.anchor, a regular file holding exactly one LF-ended JSON object
{"seq": <tip seq>, "digest": <sha256 hex of the tip line>}; it must name the record's last line
exactly (a missing, stale, mismatched or malformed anchor is cannot-evaluate). The chain and anchor
detect an edit, truncation or reorder by a writer that did not also rewrite them; they authenticate
nothing (every orchestrator worker shares one account).

Line 0, the header: seq, prev, schema ("aiqt-round-gate/1"), kind ("header"), item (the item id),
families_required (the pinned reviewer roster: 1 to MAX_FAMILIES unique family names), plan_rounds (the
integer 9) and policy_sha256 (64 lowercase hex, the pinned policy's digest).

Every later line, one round attempt: seq, prev, kind ("round"), item (equal to the header's), round (the
round number: zero is the baseline review, and fix rounds count up from one), attempt (from one), head
(40 lowercase hex, the head the round was dispatched to review), permit_sha256 (null on the baseline
round, otherwise a 64 lowercase hex digest bound to that one round number: a retry of the round may carry
it again, and any other round line that carries it is cannot-evaluate),
legs, grades and withdrawn. The first round line is the baseline round, attempt one; each next line is either
the same round with its attempt number plus one (a retry) or the next round number with attempt one. Any
other step is a round or attempt gap.

legs: exactly one entry per family of the header roster, no other family and no repeat. Each leg is
{family, state, head, dispatch_id, report_sha256, verdict, findings}. state is complete, degraded, empty
or missing. head is the head that leg reviewed: 40 lowercase hex on every leg that is not missing, and
null on a missing leg (it reviewed nothing). dispatch_id is 32 lowercase hex, or null only on a missing
leg; a non-null dispatch_id appears on one leg of the whole record only (two legs on one line, a retry
or a later round that repeat one are cannot-evaluate: each dispatch is its own id). report_sha256 is 64
lowercase hex on a complete leg (complete means a usable report exists, so it has a digest), 64
lowercase hex or null on a degraded or empty leg, and null on a missing leg. verdict is the family's own
verdict as its report states it: blockers, no-blockers or none. It is blockers or no-blockers on a
complete leg (a usable report states its verdict), any of the three on a degraded leg, and none on an
empty or missing leg; blockers needs at least one open finding on that leg, and no-blockers beside an
open finding is allowed (a family may report a finding it does not block on). findings is a list of {id,
status}, status open or closed, each id once per leg, and empty on an empty or missing leg; an id
withdrawn on an earlier line is never reported by a leg again (a finding raised again is a new id). A
leg finding carries no severity: a reviewer family's own grade is not part of the record. A leg
whose head differs from the line's head (or from another leg's) is recorded as it happened, not refused
here: the gate (a later release) refuses to count a round unless every leg is complete and reviewed the
line's head, and this reader guarantees that every leg head it compares is present and well formed.

grades: the orchestrator's severity, graded at source. One entry per id first graded on that line:
{id, severity, source_ref} plus optional replaces, followup and impact. severity is HIGH, MEDIUM or
LOW. The id must be reported by a leg on the same line and must never have been graded before (a regrade
is cannot-evaluate, whatever severity it names; a changed grade is a new id that replaces the old one).
replaces names an id graded on an earlier line or earlier on the same line, never the id itself.
followup is an item id other than the record's own item (a follow-up defers a finding to another
item, never to the item that is landing). impact is an object holding any of verdict_change,
user_visible_change and security_effect, each a JSON boolean (a key left out is allowed here; what it
means is decided by the gate, not by this reader). source_ref is non-empty text (see text fields below). After a line's grades, every id any leg reports
on that line must have a grade on that line or an earlier one.

withdrawn: a list of {id, basis}: the id must already be graded (on this line or earlier) and is
withdrawn at most once in the whole record; basis is non-empty text. A leg on the withdrawing line may
still list the id; no leg on a later line may.

Ids (item, family, finding and follow-up ids) are 1 to 64 ASCII characters: a letter or digit, then
letters, digits, '.', '_' and '-'. Text fields are 1 to MAX_TEXT characters, none of Unicode category Cc
(control), Cf (format), Cs (surrogate), Zl (line separator) or Zp (paragraph separator), and at least
one of them visible: of a Unicode category L (letter), N (number), P (punctuation) or S (symbol), and
not one of the blank-looking fillers in BLANK_LOOKING (U+115F, U+1160, U+3164 and U+FFA0, the Hangul
fillers, and U+2800, the blank Braille pattern). So whitespace alone, a combining mark alone, a filler
alone or a private-use or unassigned character alone is refused. A space separator (Zs, such as U+0020,
U+00A0 or U+3000), a combining mark, a filler, a private-use and an unassigned character are allowed
beside a visible one.

FILE ACCESS. The record path is absolute and normalized. Every directory on it is opened without
following a symlink (O_NOFOLLOW at each component, from /), and the record and anchor must each be a
regular file that is neither a symlink, a FIFO, a device nor a directory, at most its size cap; the entry
is inspected before it is opened, opened non-blocking without following a link, and must still be the
same file when opened. The directories are opened with O_PATH where the platform has it, so a directory
the account may search but not list is walked. An unreadable path, a missing file and a failed read are cannot-evaluate.

RESIDUALS. A same-account writer can rewrite a record and its anchor together; the reader detects
nothing then. The line and leg head fields are checked for shape only (that a head exists in a
repository or descends from the previous head is not checked here, and that a leg really reviewed the
head it names rests on the orchestrator that wrote the line). A dispatch_id is unique within the one
record it is read from; that a dispatch with that id was ever claimed, or that another item's record
does not hold it too, is not checked here. A leg's verdict is checked against its own findings only;
that it is the verdict the report states rests on the orchestrator that wrote the line, and what a
verdict means for counting is decided by the gate. A permit digest is bound to one round number within
the record; that it names a real permit is not checked here. A text field that holds a visible
character can still be meaningless (BLANK_LOOKING names the known blank-looking fillers, not every
glyph a font may draw blank). Whether the record is complete with respect to the reviews that
actually ran is outside what a reader can see.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: orch_round_gate.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import json
import os
import re
import reprlib
import stat
import unicodedata

SCHEMA = "aiqt-round-gate/1"
PLAN_ROUNDS = 9
EXIT_VALID = 0
EXIT_CANNOT_EVALUATE = 2
ZERO_DIGEST = "0" * 64
MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_ANCHOR_BYTES = 4096
MAX_FAMILIES = 16
MAX_ENTRIES = 4096
MAX_ROUND = 100000
MAX_ATTEMPT = 1000
MAX_TEXT = 1024
MAX_DEPTH = 8
MAX_REASON = 2048

STATES = frozenset({"complete", "degraded", "empty", "missing"})
STATUSES = frozenset({"open", "closed"})
SEVERITIES = frozenset({"HIGH", "MEDIUM", "LOW"})
VERDICTS = frozenset({"blockers", "no-blockers", "none"})
# Characters of a visible Unicode category (L, N, P or S) that render blank: the Hangul fillers (Lo) and
# the blank Braille pattern (So). A text field needs one visible character that is not one of these.
BLANK_LOOKING = frozenset({0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800})

# Field names, held as constants so every read below goes through one name.
F_SEQ, F_PREV, F_KIND, F_ITEM = "seq", "prev", "kind", "item"
F_SCHEMA, F_ROSTER, F_PLAN, F_POLICY = "schema", "families_required", "plan_rounds", "policy_sha256"
F_ROUND, F_ATTEMPT, F_HEAD, F_PERMIT = "round", "attempt", "head", "permit_sha256"
F_LEGS, F_GRADES, F_WITHDRAWN = "legs", "grades", "withdrawn"
F_FAMILY, F_STATE, F_DISPATCH, F_REPORT, F_FINDINGS = (
    "family", "state", "dispatch_id", "report_sha256", "findings")
F_ID, F_STATUS, F_SEVERITY, F_SOURCE = "id", "status", "severity", "source_ref"
F_REPLACES, F_FOLLOWUP, F_IMPACT, F_BASIS = "replaces", "followup", "impact", "basis"
F_DIGEST, F_VERDICT = "digest", "verdict"
IMPACT_KEYS = frozenset({"verdict_change", "user_visible_change", "security_effect"})

HEADER_KEYS = frozenset({F_SEQ, F_PREV, F_SCHEMA, F_KIND, F_ITEM, F_ROSTER, F_PLAN, F_POLICY})
ROUND_KEYS = frozenset({F_SEQ, F_PREV, F_KIND, F_ITEM, F_ROUND, F_ATTEMPT, F_HEAD, F_PERMIT, F_LEGS,
                        F_GRADES, F_WITHDRAWN})
LEG_KEYS = frozenset({F_FAMILY, F_STATE, F_HEAD, F_DISPATCH, F_REPORT, F_VERDICT, F_FINDINGS})
FINDING_KEYS = frozenset({F_ID, F_STATUS})
GRADE_REQUIRED = frozenset({F_ID, F_SEVERITY, F_SOURCE})
GRADE_OPTIONAL = frozenset({F_REPLACES, F_FOLLOWUP, F_IMPACT})
WITHDRAWN_KEYS = frozenset({F_ID, F_BASIS})
ANCHOR_KEYS = frozenset({F_SEQ, F_DIGEST})

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)
HEX32_RE = re.compile(r"[0-9a-f]{32}", re.ASCII)
HEX40_RE = re.compile(r"[0-9a-f]{40}", re.ASCII)
HEX64_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
BOM = b"\xef\xbb\xbf"


# The one formatter for every value a reason quotes (a record value, a field name, an argument, a path or
# an exception's message): reprlib bounds its nesting level and its length, so no value can overflow the
# stack or swell the reason.
_SHOW = reprlib.Repr(maxlevel=3, maxdict=4, maxlist=6, maxtuple=6, maxset=6, maxfrozenset=6,
                     maxstring=80, maxlong=40, maxother=80)
_CUT = " ... [reason cut at {} characters]".format(MAX_REASON)


def _show(value):
    """value for a reason: bounded by _SHOW, or its type alone when even that fails."""
    try:
        return _SHOW.repr(value)
    except Exception:
        return "<a {} value>".format(type(value).__name__)


def _describe(exc):
    """An exception for a reason: its type name and its message, bounded by _show."""
    try:
        text = str(exc)
    except Exception:
        text = ""
    return "{}: {}".format(type(exc).__name__, _show(text))


def _bounded(reason):
    """reason, cut to at most MAX_REASON characters with a marker. Every reason passes through here: the
    values in it are already bounded by _show, so the cut is a last bound, never the usual case."""
    text = reason if type(reason) is str else _show(reason)
    if len(text) <= MAX_REASON:
        return text
    return text[:MAX_REASON - len(_CUT)] + _CUT


class CannotEvaluate(Exception):
    """The record cannot be evaluated: unreadable, malformed or contradictory. Always exit 2. Its reason
    is bounded (_bounded) when it is raised."""

    def __init__(self, reason):
        super().__init__(_bounded(reason))


# ---------- file access ----------

def _cloexec():
    return getattr(os, "O_CLOEXEC", 0)


def _dir_flags():
    """Flags for one directory of the walk: O_PATH where the platform has it, so a directory the
    account may search but not list (mode --x) is still walked; never following a symlink."""
    return getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | _cloexec()


def read_regular(path, max_bytes, what):
    """The bytes of the regular file at the absolute, normalized path, read without following a symlink
    at any component, or CannotEvaluate. The entry is inspected (lstat through the parent descriptor)
    before it is opened, so a FIFO or device is never opened; the open is non-blocking and O_NOFOLLOW,
    and the opened file must be the inspected one."""
    if (not isinstance(path, str) or not path or not os.path.isabs(path) or path.startswith("//")
            or os.path.normpath(path) != path or "\x00" in path):
        raise CannotEvaluate("the {} path {} must be absolute and normalized".format(what, _show(path)))
    parts = path.split("/")[1:]
    shown = _show(path)
    if not parts or not parts[-1]:
        raise CannotEvaluate("the {} path {} names no file".format(what, shown))
    if os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd:
        raise CannotEvaluate("this platform cannot open a path one component at a time")
    try:
        fd = os.open("/", _dir_flags())
    except OSError as exc:
        raise CannotEvaluate("cannot open / to read the {}: {}".format(what, _describe(exc)))
    try:
        for comp in parts[:-1]:
            try:
                nfd = os.open(comp, _dir_flags(), dir_fd=fd)
            except OSError as exc:
                raise CannotEvaluate("the {} path component {} is not a plain directory (a symlink "
                                     "is refused) or cannot be opened: {}".format(
                                         what, _show(comp), _describe(exc)))
            os.close(fd)
            fd = nfd
            try:
                is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
            except OSError as exc:
                raise CannotEvaluate("the {} path component {} cannot be inspected: {}".format(
                    what, _show(comp), _describe(exc)))
            if not is_dir:
                raise CannotEvaluate("the {} path component {} is not a plain directory (a symlink "
                                     "is refused)".format(what, _show(comp)))
        name = parts[-1]
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError as exc:
            raise CannotEvaluate("the {} {} is unreadable: {}".format(what, shown, _describe(exc)))
        if not stat.S_ISREG(st.st_mode):
            raise CannotEvaluate("the {} {} is not a regular file (a symlink, FIFO, device or "
                                 "directory is refused)".format(what, shown))
        if st.st_size > max_bytes:
            raise CannotEvaluate("the {} {} is {} bytes, over its {}-byte cap".format(
                what, shown, st.st_size, max_bytes))
        try:
            ffd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _cloexec(), dir_fd=fd)
        except OSError as exc:
            raise CannotEvaluate("the {} {} is unreadable: {}".format(what, shown, _describe(exc)))
        try:
            fst = os.fstat(ffd)
            if not stat.S_ISREG(fst.st_mode) or (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
                raise CannotEvaluate("the {} {} changed between inspection and open".format(what, shown))
            chunks, total = [], 0
            while True:
                try:
                    block = os.read(ffd, min(65536, max_bytes + 1 - total))
                except OSError as exc:
                    raise CannotEvaluate("the {} {} could not be read: {}".format(
                        what, shown, _describe(exc)))
                if not block:
                    break
                chunks.append(block)
                total += len(block)
                if total > max_bytes:
                    raise CannotEvaluate("the {} {} grew over its {}-byte cap while read".format(
                        what, shown, max_bytes))
            return b"".join(chunks)
        finally:
            os.close(ffd)
    finally:
        os.close(fd)


# ---------- strict JSON ----------

def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise CannotEvaluate("duplicate key {}".format(_show(key)))
        obj[key] = value
    return obj


def _too_deep(value, limit):
    """True when a list or object in value sits more than limit levels deep (value itself is level one).
    Iterative, so a value of any depth the decoder returned is measured without recursion."""
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        children = node.values() if isinstance(node, dict) else node
        if depth > limit:
            return True
        for child in children:
            if isinstance(child, (dict, list)):
                stack.append((child, depth + 1))
    return False


def _constant(name):
    raise CannotEvaluate("non-finite number {} is not JSON this record accepts".format(name))


def strict_json(text, where):
    """One JSON object from text, refusing duplicate keys, NaN, Infinity and nesting deeper than
    MAX_DEPTH; CannotEvaluate otherwise. The depth is checked before any check reads the value, so no
    later check meets a value nested deeper than the record's form allows."""
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except CannotEvaluate as exc:
        raise CannotEvaluate("{}: {}".format(where, exc))
    except RecursionError:
        raise CannotEvaluate("{}: nested too deeply for the JSON decoder".format(where))
    except ValueError as exc:
        raise CannotEvaluate("{}: invalid JSON ({})".format(where, _describe(exc)))
    if not isinstance(value, dict):
        raise CannotEvaluate("{}: a line must be one JSON object".format(where))
    if _too_deep(value, MAX_DEPTH):
        raise CannotEvaluate("{}: nested too deeply (a list or object more than {} levels deep)".format(
            where, MAX_DEPTH))
    return value


# ---------- field checks ----------

def _keys(obj, required, optional, where):
    if not isinstance(obj, dict):
        raise CannotEvaluate("{}: must be an object".format(where))
    present = set(obj)
    missing = sorted(required - present)
    unknown = sorted(present - required - optional)
    if missing:
        raise CannotEvaluate("{}: missing field(s) {}".format(where, _show(missing)))
    if unknown:
        raise CannotEvaluate("{}: {} unknown field(s) {}".format(where, len(unknown), _show(unknown)))


def _int(value, low, high, where):
    if type(value) is not int or not low <= value <= high:
        raise CannotEvaluate("{}: must be an integer from {} to {} (a boolean or float is refused), "
                             "got {}".format(where, low, high, _show(value)))
    return value


def _match(value, pattern, where, shape):
    if type(value) is not str or not pattern.fullmatch(value):
        raise CannotEvaluate("{}: must be {}, got {}".format(where, shape, _show(value)))
    return value


def _ident(value, where):
    return _match(value, ID_RE, where, "an id (1 to 64 of [A-Za-z0-9._-], a letter or digit first)")


def _hex(value, pattern, width, where, nullable=False):
    if nullable and value is None:
        return None
    return _match(value, pattern, where, "{} lowercase hex{}".format(width, " or null" if nullable else ""))


def _visible(ch):
    """True for a character that shows: of a Unicode category L, N, P or S, and not blank-looking."""
    return unicodedata.category(ch)[0] in "LNPS" and ord(ch) not in BLANK_LOOKING


def _text(value, where):
    if type(value) is not str or not 1 <= len(value) <= MAX_TEXT or not any(
            _visible(ch) for ch in value) or any(
            unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp") for ch in value):
        raise CannotEvaluate("{}: must be 1 to {} characters of text, at least one not whitespace and "
                             "visible (Unicode category L, N, P or S, and not a blank-looking filler "
                             "such as U+3164 or U+2800), with no control (Cc), format (Cf), surrogate "
                             "(Cs), line separator (Zl) or paragraph separator (Zp) character".format(
                                 where, MAX_TEXT))
    return value


def _closed(value, vocabulary, where):
    if type(value) is not str or value not in vocabulary:
        raise CannotEvaluate("{}: {} is not one of {}".format(
            where, _show(value), _show(sorted(vocabulary))))
    return value


def _list(value, where, high=MAX_ENTRIES):
    if type(value) is not list or len(value) > high:
        raise CannotEvaluate("{}: must be a list of at most {} entries".format(where, high))
    return value


# ---------- the record ----------

def _check_header(obj, where):
    _keys(obj, HEADER_KEYS, frozenset(), where)
    _closed(obj[F_KIND], frozenset({"header"}), where + " kind")
    if obj[F_SCHEMA] != SCHEMA or type(obj[F_SCHEMA]) is not str:
        raise CannotEvaluate("{} schema: must be {!r}, got {}".format(where, SCHEMA, _show(obj[F_SCHEMA])))
    _ident(obj[F_ITEM], where + " item")
    roster = _list(obj[F_ROSTER], where + " families_required", MAX_FAMILIES)
    if not roster:
        raise CannotEvaluate("{} families_required: must name at least one family".format(where))
    seen = set()
    for index, family in enumerate(roster):
        _ident(family, "{} families_required[{}]".format(where, index))
        if family in seen:
            raise CannotEvaluate("{} families_required: family {} is listed twice".format(
                where, _show(family)))
        seen.add(family)
    if type(obj[F_PLAN]) is not int or obj[F_PLAN] != PLAN_ROUNDS:
        raise CannotEvaluate("{} plan_rounds: must be the integer {}, got {}".format(
            where, PLAN_ROUNDS, _show(obj[F_PLAN])))
    _hex(obj[F_POLICY], HEX64_RE, 64, where + " policy_sha256")
    return obj[F_ITEM], frozenset(roster)


class Ledger:
    """What the lines before the current one established, each looked up, never copied: graded (id ->
    severity), withdrawn (ids), dispatches (every non-null dispatch_id) and permits (permit digest -> the
    round number it is bound to)."""

    __slots__ = ("graded", "withdrawn", "dispatches", "permits")

    def __init__(self, graded=None):
        self.graded = {} if graded is None else graded
        self.withdrawn = set()
        self.dispatches = set()
        self.permits = {}


def _check_round(obj, where, item, roster, ledger, previous):
    """Validate one round line against the header and the lines before it (ledger, updated in place);
    returns (round, attempt)."""
    graded = ledger.graded
    _keys(obj, ROUND_KEYS, frozenset(), where)
    _closed(obj[F_KIND], frozenset({"round"}), where + " kind")
    if _ident(obj[F_ITEM], where + " item") != item:
        raise CannotEvaluate("{} item: {} differs from the header's item {}".format(
            where, _show(obj[F_ITEM]), _show(item)))
    rnd = _int(obj[F_ROUND], 0, MAX_ROUND, where + " round")
    attempt = _int(obj[F_ATTEMPT], 1, MAX_ATTEMPT, where + " attempt")
    if previous is None:
        if (rnd, attempt) != (0, 1):
            raise CannotEvaluate("{}: the first round line must be the baseline (round number zero, "
                                 "attempt one), got round {} attempt {}".format(where, rnd, attempt))
    else:
        prev_round, prev_attempt = previous
        if not ((rnd == prev_round and attempt == prev_attempt + 1)
                or (rnd == prev_round + 1 and attempt == 1)):
            raise CannotEvaluate("{}: round {} attempt {} does not follow round {} attempt {} (a round or "
                                 "attempt gap)".format(where, rnd, attempt, prev_round, prev_attempt))
    _hex(obj[F_HEAD], HEX40_RE, 40, where + " head")
    if rnd == 0:
        if obj[F_PERMIT] is not None:
            raise CannotEvaluate("{} permit_sha256: must be null on the baseline round".format(where))
    else:
        permit = _hex(obj[F_PERMIT], HEX64_RE, 64, where + " permit_sha256 (a fix round)")
        bound = ledger.permits.get(permit)
        if bound is not None and bound != rnd:
            raise CannotEvaluate("{} permit_sha256: {} is bound to round {} already (a permit digest "
                                 "names one round; only that round's retries carry it again)".format(
                                     where, _show(permit), bound))
        ledger.permits[permit] = rnd

    legs = _list(obj[F_LEGS], where + " legs", MAX_FAMILIES)
    families, reported = set(), set()
    for index, leg in enumerate(legs):
        lw = "{} legs[{}]".format(where, index)
        _keys(leg, LEG_KEYS, frozenset(), lw)
        family = _ident(leg[F_FAMILY], lw + " family")
        if family not in roster:
            raise CannotEvaluate("{} family: {} is not in the header roster {}".format(
                lw, _show(family), _show(sorted(roster))))
        if family in families:
            raise CannotEvaluate("{} family: {} has two legs on one line".format(lw, _show(family)))
        families.add(family)
        state = _closed(leg[F_STATE], STATES, lw + " state")
        verdict = _closed(leg[F_VERDICT], VERDICTS, lw + " verdict")
        _hex(leg[F_HEAD], HEX40_RE, 40, lw + " head (the head this leg reviewed)",
             nullable=(state == "missing"))
        if state == "missing" and leg[F_HEAD] is not None:
            raise CannotEvaluate("{}: a missing leg carries no head (it reviewed nothing)".format(lw))
        dispatch = _hex(leg[F_DISPATCH], HEX32_RE, 32, lw + " dispatch_id", nullable=(state == "missing"))
        if dispatch is not None:
            if dispatch in ledger.dispatches:
                raise CannotEvaluate("{} dispatch_id: {} is already carried by another leg, on this line "
                                     "or an earlier one (each dispatch has its own id: a retry or a later "
                                     "round is a new dispatch)".format(lw, _show(dispatch)))
            ledger.dispatches.add(dispatch)
        _hex(leg[F_REPORT], HEX64_RE, 64, lw + " report_sha256", nullable=True)
        if state == "complete" and leg[F_REPORT] is None:
            raise CannotEvaluate("{}: a complete leg carries its report_sha256 (complete means a usable "
                                 "report exists)".format(lw))
        if state == "missing" and leg[F_REPORT] is not None:
            raise CannotEvaluate("{}: a missing leg carries no report_sha256".format(lw))
        findings = _list(leg[F_FINDINGS], lw + " findings")
        if state in ("empty", "missing") and findings:
            raise CannotEvaluate("{}: an {} leg carries no findings".format(lw, state))
        leg_ids, has_open = set(), False
        for findex, finding in enumerate(findings):
            fw = "{} findings[{}]".format(lw, findex)
            _keys(finding, FINDING_KEYS, frozenset(), fw)
            fid = _ident(finding[F_ID], fw + " id")
            if _closed(finding[F_STATUS], STATUSES, fw + " status") == "open":
                has_open = True
            if fid in leg_ids:
                raise CannotEvaluate("{}: finding {} is reported twice by one leg".format(fw, _show(fid)))
            if fid in ledger.withdrawn:
                raise CannotEvaluate("{}: finding {} was withdrawn on an earlier line and is reported "
                                     "again (a withdrawn id stays withdrawn; a finding raised again is a "
                                     "new id)".format(fw, _show(fid)))
            leg_ids.add(fid)
        if state in ("empty", "missing") and verdict != "none":
            raise CannotEvaluate("{} verdict: a leg in state {} states no verdict (none), got {}".format(
                lw, state, _show(verdict)))
        if state == "complete" and verdict == "none":
            raise CannotEvaluate("{} verdict: a complete leg carries its family's verdict (blockers or "
                                 "no-blockers; a usable report states one)".format(lw))
        if verdict == "blockers" and not has_open:
            raise CannotEvaluate("{} verdict: blockers with no open finding on the leg (a blocking "
                                 "verdict names at least one open finding)".format(lw))
        reported |= leg_ids
    if families != roster:
        raise CannotEvaluate("{} legs: families {} differ from the header roster {} (a family that did "
                             "not report is a leg with state missing)".format(
                                 where, _show(sorted(families)), _show(sorted(roster))))

    for index, grade in enumerate(_list(obj[F_GRADES], where + " grades")):
        gw = "{} grades[{}]".format(where, index)
        _keys(grade, GRADE_REQUIRED, GRADE_OPTIONAL, gw)
        gid = _ident(grade[F_ID], gw + " id")
        if gid in graded:
            raise CannotEvaluate("{}: {} is already graded {}; a regrade is refused (a changed grade "
                                 "is a new id that replaces it)".format(gw, _show(gid), graded[gid]))
        if gid not in reported:
            raise CannotEvaluate("{}: {} is graded but no leg on this line reports it (a grade is "
                                 "set at its source)".format(gw, _show(gid)))
        severity = _closed(grade[F_SEVERITY], SEVERITIES, gw + " severity")
        _text(grade[F_SOURCE], gw + " source_ref")
        if F_REPLACES in grade:
            target = _ident(grade[F_REPLACES], gw + " replaces")
            if target == gid or target not in graded:
                raise CannotEvaluate("{} replaces: {} must name an id graded before this one, not the "
                                     "id itself".format(gw, _show(target)))
        if F_FOLLOWUP in grade:
            if _ident(grade[F_FOLLOWUP], gw + " followup") == item:
                raise CannotEvaluate("{} followup: {} is the record's own item (a follow-up names "
                                     "another item)".format(gw, _show(item)))
        if F_IMPACT in grade:
            impact = grade[F_IMPACT]
            _keys(impact, frozenset(), IMPACT_KEYS, gw + " impact")
            for key in sorted(impact):
                if type(impact[key]) is not bool:
                    raise CannotEvaluate("{} impact {}: must be a JSON boolean, got {}".format(
                        gw, key, _show(impact[key])))
        graded[gid] = severity
    # Each reported id is looked up in graded; copying graded on every line would make a record's run
    # time grow with the square of its length.
    ungraded = sorted(fid for fid in reported if fid not in graded)
    if ungraded:
        raise CannotEvaluate("{}: {} reported finding(s) {} have no grade on this line or an earlier one "
                             "(severity is the orchestrator's grade, set at source)".format(
                                 where, len(ungraded), _show(ungraded)))

    for index, entry in enumerate(_list(obj[F_WITHDRAWN], where + " withdrawn")):
        ww = "{} withdrawn[{}]".format(where, index)
        _keys(entry, WITHDRAWN_KEYS, frozenset(), ww)
        wid = _ident(entry[F_ID], ww + " id")
        _text(entry[F_BASIS], ww + " basis")
        if wid not in graded:
            raise CannotEvaluate("{}: {} is withdrawn but was never graded".format(ww, _show(wid)))
        if wid in ledger.withdrawn:
            raise CannotEvaluate("{}: {} is already withdrawn".format(ww, _show(wid)))
        ledger.withdrawn.add(wid)
    return rnd, attempt


def validate_bytes(data, anchor_data):
    """Validate a record's bytes and its anchor's bytes; return a summary dict or raise CannotEvaluate."""
    if not data:
        raise CannotEvaluate("the record is empty (it needs at least its header line)")
    if data.startswith(BOM):
        raise CannotEvaluate("the record starts with a byte order mark")
    if b"\r" in data:
        raise CannotEvaluate("the record holds a carriage return (only LF ends a line)")
    try:
        data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("the record is not UTF-8: {}".format(_describe(exc)))
    if not data.endswith(b"\n"):
        raise CannotEvaluate("the record's last line has no LF (a truncated append)")
    raws = data[:-1].split(b"\n")
    item, roster, ledger, previous = None, None, Ledger(), None
    prev_digest = ZERO_DIGEST
    for index, raw in enumerate(raws):
        where = "line {}".format(index + 1)
        if not raw.strip():
            raise CannotEvaluate("{}: a blank line".format(where))
        if len(raw) > MAX_LINE_BYTES:
            raise CannotEvaluate("{}: {} bytes, over the {}-byte line cap".format(
                where, len(raw), MAX_LINE_BYTES))
        obj = strict_json(raw.decode("utf-8"), where)
        if F_SEQ not in obj or F_PREV not in obj:
            raise CannotEvaluate("{}: missing seq or prev".format(where))
        if _int(obj[F_SEQ], 0, len(raws), where + " seq") != index:
            raise CannotEvaluate("{} seq: {} where {} was due (a seq gap or reorder)".format(
                where, obj[F_SEQ], index))
        if _hex(obj[F_PREV], HEX64_RE, 64, where + " prev") != prev_digest:
            raise CannotEvaluate("{} prev: does not match the sha256 of the line before it (a broken "
                                 "chain)".format(where))
        if index == 0:
            item, roster = _check_header(obj, where)
        else:
            previous = _check_round(obj, where, item, roster, ledger, previous)
        prev_digest = hashlib.sha256(raw).hexdigest()

    tip = len(raws) - 1
    if not anchor_data or b"\r" in anchor_data or not anchor_data.endswith(b"\n") or \
            anchor_data.count(b"\n") != 1:
        raise CannotEvaluate("the anchor must be exactly one LF-ended line")
    try:
        anchor_text = anchor_data[:-1].decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("the anchor is not UTF-8: {}".format(_describe(exc)))
    anchor = strict_json(anchor_text, "the anchor")
    _keys(anchor, ANCHOR_KEYS, frozenset(), "the anchor")
    _int(anchor[F_SEQ], 0, MAX_ROUND * MAX_ATTEMPT, "the anchor seq")
    _hex(anchor[F_DIGEST], HEX64_RE, 64, "the anchor digest")
    if anchor[F_SEQ] != tip or anchor[F_DIGEST] != prev_digest:
        raise CannotEvaluate("the anchor (seq {}) does not name the record's last line (seq {}) and its "
                             "digest exactly (stale, truncated or rewritten)".format(anchor[F_SEQ], tip))
    return {"item": item, "lines": len(raws), "tip_seq": tip,
            "last_round": None if previous is None else previous[0],
            "last_attempt": None if previous is None else previous[1]}


def validate_record(path, max_bytes=MAX_RECORD_BYTES):
    """Read and validate the record at path and its <path>.anchor; summary dict or CannotEvaluate."""
    data = read_regular(path, max_bytes, "record")
    anchor_data = read_regular(path + ".anchor", MAX_ANCHOR_BYTES, "anchor")
    return validate_bytes(data, anchor_data)


# ---------- command line ----------

def printable(text):
    """text as one line of printable ASCII, for every human-readable line the reader writes: a backslash
    becomes two, and every other character outside U+0020..U+007E (LF, CR, any control, format or line
    separator character, any non-ASCII character, a surrogate from an undecodable argv byte) becomes
    \\xNN, \\uNNNN or \\UNNNNNNNN. So no value carried in a message can add, split or end a line, and
    the escape is unambiguous."""
    out = []
    for ch in str(text):
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif 0x20 <= code < 0x7F:
            out.append(ch)
        elif code < 0x100:
            out.append("\\x{:02x}".format(code))
        elif code < 0x10000:
            out.append("\\u{:04x}".format(code))
        else:
            out.append("\\U{:08x}".format(code))
    return "".join(out)


def _emit(decision, fields, human):
    payload = {"decision": decision}
    payload.update(fields)
    # ensure_ascii: the object is one line of ASCII whatever a field holds (JSON escapes every control
    # character, line separator and non-ASCII character), and it carries each value exactly.
    sys.stdout.write(json.dumps(payload, sort_keys=True, ensure_ascii=True) + "\n")
    sys.stdout.write(printable(human) + "\n")


def _refuse(reason):
    reason = _bounded(reason)
    _emit("CANNOT_EVALUATE", {"reason": reason}, "CANNOT_EVALUATE " + reason)
    sys.stderr.write("CANNOT EVALUATE: {}\n".format(printable(reason)))
    return EXIT_CANNOT_EVALUATE


def main(argv):
    if not (len(argv) == 3 and argv[0] == "validate" and argv[1] == "--record"):
        return _refuse("usage: orch_round_gate.py validate --record ABS (any other argument list is "
                       "refused; this release has no other mode), got {}".format(_show(argv)))
    try:
        summary = validate_record(argv[2])
    except CannotEvaluate as exc:
        return _refuse(str(exc))
    except Exception as exc:
        # An error the checks above did not foresee (a RecursionError or MemoryError, say) is still one
        # refusal object on stdout and exit 2, never a crash with an empty stdout.
        return _refuse("the reader could not evaluate the record: unexpected {}".format(_describe(exc)))
    _emit("VALID", summary, "VALID item={} lines={} tip seq={} last round={} attempt={}".format(
        summary["item"], summary["lines"], summary["tip_seq"], summary["last_round"],
        summary["last_attempt"]))
    return EXIT_VALID


def _entry():
    """The process entry: any escaped exception, or a stdout that cannot be written and flushed, is exit
    2 (cannot evaluate), never a traceback's exit 1 and never a silent 0."""
    try:
        code = main(sys.argv[1:])
        sys.stdout.flush()
    except Exception as exc:
        try:
            sys.stderr.write("CANNOT EVALUATE: {}\n".format(printable(_bounded(_describe(exc)))))
            sys.stderr.flush()
        except BaseException:
            pass
        os._exit(EXIT_CANNOT_EVALUATE)
    return code


if __name__ == "__main__":
    sys.exit(_entry())
