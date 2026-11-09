#!/usr/bin/env python3
"""Brief carry lint: a derived brief carries every active row of its carry ledger, word for word.

  check_brief_carry.py --ledger ABS_LEDGER BRIEF   exit 0 match, 1 finding, 2 cannot evaluate
  check_brief_carry.py --self-test                 fixtures for the cases its PASS line names
  check_brief_carry.py --self-test --execution-report ABS_PATH
  any other argument list is refused with exit 2

A requirement a brief adds to stop one failure from recurring is easy to lose when a later brief is
derived from it. The carry ledger records such requirements outside the brief text, and this lint
refuses a brief that does not carry them. The repository holds no ledger and no brief: the sender
runs the lint on the final bytes it sends and withholds the brief on exit 1 or 2; the lint helps
only where the sender does that.

LEDGER. One ledger per brief lineage, JSON Lines, every line ending in LF, rows only appended,
never edited. Each row is one JSON object with exactly the keys seq, id, ts, gate, text, status
and prev, plus reason (optional on an active row, required and non-empty on a retired row):
  {"seq": N, "id": "<id>", "ts": "YYYY-MM-DDTHH:MM:SSZ", "gate": "<roster gate name>",
   "text": "<one line, may be empty>", "status": "active|retired", "reason": "...",
   "prev": "<sha256 of the previous raw line, or 64 zeros on the first line>"}
seq is the line number. gate is a roster gate name (an ASCII letter or digit, then letters,
digits, '.', '_' or '-'). text holds no control, format or line-separator character. An id keeps
the gate and text of its first row; the first row of an id is active; a later row of the id
changes its status (active to retired with a reason, or retired to active again), and a row
that repeats the id's current status is a violation. Rows are parsed with the mistakes-register
parser helpers (check_mistakes_register: the duplicate-key refusal, the zero digest and the
non-blocking, regular-file-only read), not a second implementation. A ledger that is malformed
in any way (a broken digest chain, a seq that is not the line number, a duplicated key, a line
that is not a JSON object, nesting too deep for the decoder, a blank line, a CR anywhere, a
missing final LF, a missing or unknown key, a bad field, an illegal status change, an id whose
gate or text changes, or two active ids with the same gate and text) is cannot-evaluate (exit
2), never a clean pass. An empty ledger has no rows and is clean.

BRIEF BLOCK. A line that is exactly CARRIED CHECKS opens the block; each following line that
starts with "CARRY " is one carried check, written CARRY <gate>: <text> (one space after the
colon, which may be left out when text is empty); the block ends at the first line that does
not start with "CARRY ". The lint compares the block's (gate, text) pairs with the ledger's
active rows as an EXACT set, with no whitespace or case normalisation, and names every
mismatch with repr. Exit 1, which the brief author fixes: an active row missing from the block
(a reworded row is missing, and the message names the text the block carries for that gate
instead), a block pair that is a retired row, a block pair that no active row holds (a pair
enters the block only through a ledger row), a CARRY line outside the block, a malformed CARRY
line, a line repeated in the block, or a second CARRIED CHECKS heading. A gate the brief lists
anywhere else, by hand, is not judged: a hand-written list may add checks but never stands in
for a carried one. Exit 2: a ledger or brief that is missing, not a regular file (a directory,
a FIFO or a device; the open is non-blocking and the opened descriptor is checked by fstat
before anything is read), not UTF-8, or holding a CR; a relative ledger path; or a malformed
ledger. A brief with no block passes only when the ledger has no active row.

RESIDUALS. The lint reads the ledger it is given and proves only that the ledger is internally
consistent: deleting the last rows of a ledger, or replacing the whole ledger, still passes the
chain check, because no anchor records the last verified row; that stays open until an appender
with validated writes and an anchor file against tail truncation land. Which ledger belongs to
which brief lineage is the sender's choice and is not checked. The ledger has no size bound
beyond what the reader can hold. An indented CARRY line is not a carry line and does not count
(the row it was meant to carry is then reported missing). The lint judges the block's words,
not whether the worker obeys them.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_brief_carry.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import datetime
import hashlib
import json
import os
import tempfile
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_mistakes_register import (  # noqa: E402
    ZERO, CannotEvaluate, _DuplicateKey, _pairs_without_duplicates, _read_following, _shown)

HEADING = "CARRIED CHECKS"
CARRY = "CARRY "
STATUSES = ("active", "retired")
REQUIRED_KEYS = frozenset(("seq", "id", "ts", "gate", "text", "status", "prev"))
OPTIONAL_KEYS = frozenset(("reason",))
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
GATE_FIRST = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
GATE_REST = GATE_FIRST | frozenset("._-")
# Characters a one-line text never holds: controls (LF and CR included), format characters such
# as a zero-width space, and the Unicode line and paragraph separators.
BAD_TEXT_CATEGORIES = frozenset(("Cc", "Cf", "Zl", "Zp"))


# ---------- reading ----------

def _absent(path, label):
    """A missing input is cannot-evaluate, never an empty one."""
    raise CannotEvaluate("{} {} does not exist".format(label, _shown(str(path))))


def _read_input(path, label):
    """The bytes of PATH through the mistakes-register reader: one non-blocking open whose opened
    descriptor must prove a regular file before anything is read. A confirmed-absent path goes to
    _absent; every other failure is CannotEvaluate."""
    try:
        return _read_following(path, label)
    except FileNotFoundError:
        return _absent(path, label)
    except OSError as exc:
        raise CannotEvaluate("{} {} cannot be opened ({})".format(label, _shown(str(path)), exc))


def _decode(data, label):
    """The text of DATA decoded as strict UTF-8; CannotEvaluate otherwise."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{} is not UTF-8 ({})".format(label, exc)) from None
    return text


# ---------- the ledger ----------

def _cr_problems(text):
    if "\r" in text:
        return ["ledger: holds a carriage return (lines end in LF only)"]
    return []


def _final_lf_problems(text):
    if text and not text.endswith("\n"):
        return ["ledger: the last line does not end in LF (a torn or truncated write)"]
    return []


def _blank_problems(n):
    return ["ledger line {}: blank line inside the ledger".format(n)]


def _decode_row(line, n):
    """(row, problems): row is the decoded object, or None when the line is not one."""
    try:
        row = json.loads(line, object_pairs_hook=_pairs_without_duplicates)
    except _DuplicateKey as exc:
        return None, ["ledger line {}: duplicated key {} in the row".format(n, _shown(exc.args[0]))]
    except ValueError:
        return None, ["ledger line {}: not a JSON object".format(n)]
    except RecursionError:
        return None, ["ledger line {}: nested too deeply for the JSON decoder".format(n)]
    if not isinstance(row, dict):
        return None, ["ledger line {}: not a JSON object".format(n)]
    return row, []


def _chain_problems(row, n, prev_line):
    want = ZERO if prev_line is None else hashlib.sha256(prev_line.encode("utf-8")).hexdigest()
    if row.get("prev") != want:
        return ["ledger line {}: digest chain broken".format(n)]
    return []


def _seq_problems(row, n):
    seq = row.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq != n:
        return ["ledger line {}: seq {} is not the line number".format(n, _shown(seq))]
    return []


def _key_problems(row, n):
    keys = set(row)
    problems = []
    missing = sorted(REQUIRED_KEYS - keys)
    unknown = sorted(keys - REQUIRED_KEYS - OPTIONAL_KEYS)
    if missing:
        problems.append("ledger line {}: missing key(s) {}".format(n, _shown(missing)))
    if unknown:
        problems.append("ledger line {}: unknown key(s) {}".format(n, _shown(unknown)))
    return problems


# Each field has its own predicate, so the self-test can switch off one field rule at a time and
# show that its case alone turns green.

def _id_ok(rid):
    return (isinstance(rid, str) and rid != "" and rid.isprintable()
            and not any(ch.isspace() for ch in rid))


def _gate_ok(gate):
    return (isinstance(gate, str) and gate != "" and gate[0] in GATE_FIRST
            and all(ch in GATE_REST for ch in gate))


def _plain_line(value):
    return isinstance(value, str) and not any(
        unicodedata.category(ch) in BAD_TEXT_CATEGORIES for ch in value)


def _text_ok(text):
    return _plain_line(text)


def _reason_ok(reason):
    return _plain_line(reason)


def _status_ok(status):
    return status in STATUSES


def _ts_ok(ts):
    if not isinstance(ts, str) or len(ts) != 20:
        return False
    try:
        datetime.datetime.strptime(ts, TS_FORMAT)
    except ValueError:
        return False
    return True


def _field_problems(row, n):
    problems = []
    rid = row.get("id")
    if not _id_ok(rid):
        problems.append("ledger line {}: id {} is not a non-empty printable word".format(
            n, _shown(rid)))
    if "ts" in row and not _ts_ok(row["ts"]):
        problems.append("ledger line {}: ts {} is not YYYY-MM-DDTHH:MM:SSZ".format(
            n, _shown(row["ts"])))
    if "gate" in row and not _gate_ok(row["gate"]):
        problems.append("ledger line {}: gate {} is not a roster gate name".format(
            n, _shown(row["gate"])))
    if "text" in row and not _text_ok(row["text"]):
        problems.append("ledger line {}: text {} is not one line of plain text".format(
            n, _shown(row["text"])))
    if "status" in row and not _status_ok(row["status"]):
        problems.append("ledger line {}: unknown status {}".format(n, _shown(row["status"])))
    if "reason" in row and not _reason_ok(row["reason"]):
        problems.append("ledger line {}: reason {} is not one line of plain text".format(
            n, _shown(row["reason"])))
    return problems


def _reason_problems(row, n):
    if row.get("status") == "retired" and not row.get("reason"):
        return ["ledger line {}: a retired row needs a non-empty reason".format(n)]
    return []


def _transition_problems(row, n, last_status):
    rid, status = row["id"], row["status"]
    if rid not in last_status:
        if status != "active":
            return ["ledger line {}: the first row of id {} must be active".format(n, _shown(rid))]
        return []
    if status == last_status[rid]:
        return ["ledger line {}: id {} repeats its current status {}".format(
            n, _shown(rid), _shown(status))]
    return []


def _rebind_problems(row, n, pair_of):
    rid = row["id"]
    pair = (row["gate"], row["text"])
    if rid in pair_of and pair_of[rid] != pair:
        return ["ledger line {}: id {} changes its gate and text from {} to {}".format(
            n, _shown(rid), _shown(pair_of[rid]), _shown(pair))]
    return []


def _current_pairs(rows):
    """{id: ((gate, text), latest status)} over well-formed rows in ledger order."""
    current = {}
    for row in rows:
        current[row["id"]] = ((row["gate"], row["text"]), row["status"])
    return current


def _duplicate_active_problems(current):
    seen, problems = {}, []
    for rid, (pair, status) in current.items():
        if status != "active":
            continue
        if pair in seen:
            problems.append("ledger: ids {} and {} are both active for the same gate and text {}".format(
                _shown(seen[pair]), _shown(rid), _shown(pair)))
        else:
            seen[pair] = rid
    return problems


def parse_ledger(text):
    """(current, problems) for the ledger TEXT: current maps each id to its (gate, text) pair and
    latest status; problems is empty only for a well-formed ledger."""
    problems = []
    problems.extend(_cr_problems(text))
    problems.extend(_final_lf_problems(text))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    rows, prev_line, last_status, pair_of = [], None, {}, {}
    for n, line in enumerate(lines, 1):
        if not line.strip():
            problems.extend(_blank_problems(n))
            prev_line = line
            continue
        row, found = _decode_row(line, n)
        problems.extend(found)
        if row is None:
            prev_line = line
            continue
        found = _chain_problems(row, n, prev_line) + _seq_problems(row, n)
        shape = _key_problems(row, n) + _field_problems(row, n)
        problems.extend(found + shape)
        prev_line = line
        if shape:
            continue
        found = (_reason_problems(row, n) + _transition_problems(row, n, last_status)
                 + _rebind_problems(row, n, pair_of))
        problems.extend(found)
        if found:
            continue
        last_status[row["id"]] = row["status"]
        pair_of.setdefault(row["id"], (row["gate"], row["text"]))
        rows.append(row)
    current = _current_pairs(rows)
    problems.extend(_duplicate_active_problems(current))
    return current, problems


# ---------- the brief ----------

def _carry_pair(line):
    """(gate, text) of a CARRY line, or None when the line is malformed."""
    rest = line[len(CARRY):]
    gate, colon, tail = rest.partition(":")
    if not colon or not _gate_ok(gate):
        return None
    if tail.startswith(" "):
        tail = tail[1:]
    elif tail:
        return None
    return gate, tail


def parse_brief(text):
    """(pairs, findings): the block's (gate, text) pairs in order and the structure findings."""
    lines = text.split("\n")
    findings, pairs, seen_lines = [], [], set()
    heading_at = None
    in_block = False
    for n, line in enumerate(lines, 1):
        if line == HEADING:
            if heading_at is not None:
                findings.append("brief line {}: a second {} heading (the first is on line {})".format(
                    n, HEADING, heading_at))
                in_block = False
                continue
            heading_at, in_block = n, True
            continue
        if not line.startswith(CARRY):
            in_block = False
            continue
        if not in_block:
            findings.append("brief line {}: a CARRY line outside the {} block: {}".format(
                n, HEADING, _shown(line)))
            continue
        if line in seen_lines:
            findings.append("brief line {}: the line {} is repeated in the block".format(n, _shown(line)))
            continue
        seen_lines.add(line)
        pair = _carry_pair(line)
        if pair is None:
            findings.append("brief line {}: malformed CARRY line {} (want CARRY <gate>: <text>)".format(
                n, _shown(line)))
            continue
        pairs.append(pair)
    return pairs, findings


# ---------- the comparison ----------

def compare(current, pairs):
    """The findings of the exact-set comparison of the block PAIRS with the active rows."""
    active = {pair: rid for rid, (pair, status) in current.items() if status == "active"}
    retired = {pair: rid for rid, (pair, status) in current.items() if status == "retired"}
    got = set(pairs)
    findings = []
    for pair in sorted(set(active) - got):
        gate, text = pair
        message = "missing carried check gate={} text={} (ledger id {})".format(
            _shown(gate), _shown(text), _shown(active[pair]))
        instead = sorted(t for g, t in got if g == gate and (g, t) not in active)
        if instead:
            message += "; the block carries {} for that gate instead (reworded)".format(
                ", ".join(_shown(t) for t in instead))
        findings.append(message)
    for pair in sorted(got - set(active)):
        if pair in retired:
            findings.append("retired row {} (ledger id {}) is still carried".format(
                _shown(pair), _shown(retired[pair])))
        else:
            findings.append("carried pair {} is not an active ledger row".format(_shown(pair)))
    return findings


def evaluate(ledger_path, brief_path):
    """(exit code, findings, errors) for one ledger and one brief; prints nothing."""
    errors = []
    if not os.path.isabs(str(ledger_path)):
        return 2, [], ["the ledger path {} is not absolute".format(_shown(str(ledger_path)))]
    current = None
    try:
        ledger_text = _decode(_read_input(ledger_path, "ledger"), "ledger")
        current, problems = parse_ledger(ledger_text)
        errors.extend(problems)
    except CannotEvaluate as exc:
        errors.append(str(exc))
    pairs = findings = None
    try:
        brief_text = _decode(_read_input(brief_path, "brief"), "brief")
        if "\r" in brief_text:
            raise CannotEvaluate("brief holds a carriage return (lines end in LF only)")
        pairs, findings = parse_brief(brief_text)
    except CannotEvaluate as exc:
        errors.append(str(exc))
    if errors:
        return 2, [], errors
    findings = findings + compare(current, pairs)
    return (1 if findings else 0), findings, []


def run(ledger_path, brief_path):
    code, findings, errors = evaluate(ledger_path, brief_path)
    for line in errors:
        print("CANNOT EVALUATE: " + line, file=sys.stderr)
    for line in findings:
        print("FAIL: " + line)
    if code == 0:
        print("PASS: the brief carries every active ledger row exactly")
    return code


# ---------- self-test ----------

# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id
# record the instrumented check() helper appends to; the execution gate reconciles the report
# against the registry.
SUITE_ID = "brief-carry-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


A_GATE, A_TEXT = "brief-capability-selftest", "gate sentences use the indicative"
B_GATE, B_TEXT = "no-dashes", "no dash characters in any changed file"


def _row(rid, gate, text, status="active", **extra):
    row = {"id": rid, "ts": "2026-11-01T00:00:00Z", "gate": gate, "text": text, "status": status}
    row.update(extra)
    return row


def _chain(rows):
    """The ledger lines for ROWS, seq and prev filled in, each row dict copied in order."""
    lines, prev = [], None
    for n, row in enumerate(rows, 1):
        full = {"seq": n}
        full.update(row)
        full["prev"] = ZERO if prev is None else hashlib.sha256(prev.encode("utf-8")).hexdigest()
        full.update(row.get("_override", {}))
        full.pop("_override", None)
        line = json.dumps(full)
        lines.append(line)
        prev = line
    return lines


def _ledger(rows):
    return "".join(line + "\n" for line in _chain(rows))


def _brief(carry_lines, before="Task: fix the thing.\n\n", after="\nEnd of brief.\n"):
    if carry_lines is None:
        return before + after
    return before + HEADING + "\n" + "".join(line + "\n" for line in carry_lines) + after


def _carry(gate, text):
    return "{}{}: {}".format(CARRY, gate, text)


def _write(path, content):
    with open(path, "wb") as handle:
        handle.write(content.encode("utf-8") if isinstance(content, str) else content)


def _case(ledger, brief):
    """(exit code, findings, errors) for LEDGER and BRIEF content (str or bytes) in a tempdir."""
    with tempfile.TemporaryDirectory(prefix="brief-carry-selftest-") as tmp:
        ledger_path = os.path.join(tmp, "carry.jsonl")
        brief_path = os.path.join(tmp, "brief.md")
        _write(ledger_path, ledger)
        _write(brief_path, brief)
        return evaluate(ledger_path, brief_path)


def _flipped(name, replacement, ledger, brief):
    """The exit code of _case under one attribute patch of this module, restored after."""
    module = sys.modules[__name__]
    original = getattr(module, name)
    setattr(module, name, replacement)
    try:
        return _case(ledger, brief)[0]
    finally:
        setattr(module, name, original)


def _drop_problems(line, n):
    return _real_decode_row(line, n)[0], []


def _nothing(*_args):
    return []


def _always_true(*_args):
    return True


def _every_status_active(rows):
    return {rid: (pair, "active") for rid, (pair, _status) in _real_current_pairs(rows).items()}


def _first_retirement_sticks(rows):
    current = {}
    for row in rows:
        rid = row["id"]
        if rid in current and current[rid][1] == "retired":
            continue
        current[rid] = ((row["gate"], row["text"]), row["status"])
    return current


def _absent_is_empty(_path, _label):
    return b""


_real_decode_row = _decode_row
_real_current_pairs = _current_pairs


def _self_test():
    both = _ledger([_row("CC-1", A_GATE, A_TEXT), _row("CC-2", B_GATE, B_TEXT)])
    full = _brief([_carry(A_GATE, A_TEXT), _carry(B_GATE, B_TEXT)])
    dropped = _brief([_carry(A_GATE, A_TEXT)])

    # ---------- carry: the incident and its clean control ----------
    code, findings, errors = _case(both, dropped)
    check("carry/incident-drop-refused", code == 1 and len(findings) == 1
          and repr(B_GATE) in findings[0] and repr(B_TEXT) in findings[0] and errors == [],
          (code, findings, errors))
    code, findings, errors = _case(both, full)
    check("carry/incident-restored-ok", (code, findings, errors) == (0, [], []), (code, findings, errors))
    got = _flipped("compare", _nothing, both, dropped)
    check("carry/flip-compare-silences", got == 0, got)

    # ---------- carry: a reworded row, either way, and the exact control ----------
    imperative = A_TEXT.replace("indicative", "imperative")
    results = []
    for brief in (_brief([_carry(A_GATE, imperative), _carry(B_GATE, B_TEXT)]),
                  _brief([_carry(A_GATE, A_TEXT + " "), _carry(B_GATE, B_TEXT)])):
        code, findings, errors = _case(both, brief)
        results.append((code, any("reworded" in line and repr(A_TEXT) in line for line in findings),
                        _flipped("compare", _nothing, both, brief)))
    check("carry/reworded-refused", results == [(1, True, 0), (1, True, 0)], results)
    code, findings, errors = _case(both, full)
    check("carry/exact-text-ok", code == 0, (code, findings, errors))

    # ---------- carry: retired, and retired then active again ----------
    retired = _ledger([_row("CC-1", A_GATE, A_TEXT), _row("CC-2", B_GATE, B_TEXT),
                       _row("CC-2", B_GATE, B_TEXT, "retired", reason="the dash gate now covers it")])
    code, findings, errors = _case(retired, full)
    flip = _flipped("_current_pairs", _every_status_active, retired, full)
    check("carry/retired-still-carried-refused", code == 1 and len(findings) == 1
          and "still carried" in findings[0] and flip == 0, (code, findings, errors, flip))
    code, findings, errors = _case(retired, dropped)
    check("carry/retired-omitted-ok", code == 0, (code, findings, errors))
    reactivated = _ledger([_row("CC-1", A_GATE, A_TEXT), _row("CC-2", B_GATE, B_TEXT),
                           _row("CC-2", B_GATE, B_TEXT, "retired", reason="covered elsewhere"),
                           _row("CC-2", B_GATE, B_TEXT, "active", reason="the cover was removed")])
    code, findings, errors = _case(reactivated, dropped)
    flip = _flipped("_current_pairs", _first_retirement_sticks, reactivated, dropped)
    check("carry/reactivated-required", code == 1 and len(findings) == 1
          and repr(B_TEXT) in findings[0] and flip == 0, (code, findings, errors, flip))
    code, findings, errors = _case(reactivated, full)
    check("carry/reactivated-carried-ok", code == 0, (code, findings, errors))

    # ---------- carry: a pair no active row holds ----------
    code, findings, errors = _case(_ledger([_row("CC-1", A_GATE, A_TEXT)]), full)
    check("carry/extra-pair-refused", code == 1 and len(findings) == 1
          and "not an active ledger row" in findings[0], (code, findings, errors))

    # ---------- ledger: each kind of damage is cannot-evaluate, and its flip moves it off 2 ----------
    one = [_row("CC-1", A_GATE, A_TEXT)]
    one_brief = _brief([_carry(A_GATE, A_TEXT)])
    lines = _chain(one)
    deep = "[" * 200000 + "]" * 200000
    for name, ledger, brief, flip_name, flip in (
            ("ledger/retired-no-reason",
             _ledger(one + [_row("CC-2", B_GATE, B_TEXT), _row("CC-2", B_GATE, B_TEXT, "retired")]),
             one_brief, "_reason_problems", _nothing),
            ("ledger/chain-broken",
             _ledger(one + [_row("CC-2", B_GATE, B_TEXT, _override={"prev": "f" * 64})]),
             full, "_chain_problems", _nothing),
            ("ledger/duplicate-key", lines[0][:-1] + ', "gate": ' + json.dumps(A_GATE) + "}\n",
             one_brief, "_pairs_without_duplicates", dict),
            ("ledger/bad-json", lines[0] + "\n" + '{"seq": 2,\n', one_brief, "_decode_row",
             _drop_problems),
            ("ledger/seq-gap",
             _ledger(one + [_row("CC-2", B_GATE, B_TEXT, _override={"seq": 3})]),
             full, "_seq_problems", _nothing),
            ("ledger/too-deep", lines[0] + "\n" + deep + "\n", one_brief, "_decode_row",
             _drop_problems),
            ("ledger/first-row-retired",
             _ledger([_row("CC-1", A_GATE, A_TEXT, "retired", reason="never needed")]),
             _brief(None), "_transition_problems", _nothing),
            ("ledger/repeated-status", _ledger(one + [_row("CC-1", A_GATE, A_TEXT)]),
             one_brief, "_transition_problems", _nothing),
            ("ledger/cr-in-line", lines[0] + "\r\n", one_brief, "_cr_problems", _nothing),
            ("ledger/blank-line", lines[0] + "\n\n", one_brief, "_blank_problems", _nothing),
            ("ledger/duplicate-active-pair",
             _ledger(one + [_row("CC-2", A_GATE, A_TEXT)]), one_brief,
             "_duplicate_active_problems", _nothing),
            ("ledger/no-final-newline", lines[0], one_brief, "_final_lf_problems", _nothing),
            ("ledger/unknown-key", _ledger([_row("CC-1", A_GATE, A_TEXT, note="x")]), one_brief,
             "_key_problems", _nothing),
            ("ledger/missing-key", _ledger([_row("CC-1", A_GATE, A_TEXT, _override={"ts": None})])
             .replace('"ts": null, ', ""), one_brief, "_key_problems", _nothing),
            ("ledger/id-rebinds-pair",
             _ledger(one + [_row("CC-1", A_GATE, A_TEXT + " now", "retired", reason="reworded")]),
             one_brief, "_rebind_problems", _nothing)):
        code, findings, errors = _case(ledger, brief)
        flipped = _flipped(flip_name, flip, ledger, brief)
        check(name, code == 2 and findings == [] and len(errors) >= 1 and flipped != 2,
              (code, errors, flipped))

    # ---------- ledger: one bad field per ledger, and its own predicate's flip passes it ----------
    zwsp = "a" + chr(0x200B) + "b"
    for name, ledger, brief, flip_name, needle in (
            ("ledger/bad-id", _ledger([_row("CC 1", A_GATE, A_TEXT)]), one_brief, "_id_ok",
             "is not a non-empty printable word"),
            ("ledger/bad-ts", _ledger([_row("CC-1", A_GATE, A_TEXT, ts="2026-11-01 00:00:00Z")]),
             one_brief, "_ts_ok", "is not YYYY-MM-DDTHH:MM:SSZ"),
            ("ledger/bad-gate", _ledger([_row("CC-1", "_leaks", A_TEXT)]),
             _brief([_carry("_leaks", A_TEXT)]), "_gate_ok", "is not a roster gate name"),
            ("ledger/control-in-text", _ledger([_row("CC-1", A_GATE, zwsp)]),
             _brief([_carry(A_GATE, zwsp)]), "_text_ok", "text "),
            ("ledger/bad-status",
             _ledger([_row("CC-1", A_GATE, A_TEXT), _row("CC-2", B_GATE, B_TEXT),
                      _row("CC-2", B_GATE, B_TEXT, "paused")]),
             dropped, "_status_ok", "unknown status 'paused'"),
            ("ledger/bad-reason",
             _ledger([_row("CC-1", A_GATE, A_TEXT, reason="first line\nsecond line")]),
             one_brief, "_reason_ok", "reason ")):
        code, findings, errors = _case(ledger, brief)
        flipped = _flipped(flip_name, _always_true, ledger, brief)
        check(name, code == 2 and findings == [] and len(errors) == 1 and needle in errors[0]
              and flipped == 0, (code, errors, flipped))
    many = [_row("CC-1", A_GATE, A_TEXT), _row("CC-2", B_GATE, B_TEXT), _row("CC-3", "leaks", "")]
    code, findings, errors = _case(
        _ledger(many), _brief([_carry(A_GATE, A_TEXT), _carry(B_GATE, B_TEXT), "CARRY leaks:"]))
    check("ledger/well-formed-ok", (code, findings, errors) == (0, [], []), (code, findings, errors))

    # ---------- input: missing, not regular, not UTF-8, CR ----------
    with tempfile.TemporaryDirectory(prefix="brief-carry-selftest-") as tmp:
        brief_path = os.path.join(tmp, "brief.md")
        _write(brief_path, _brief(None))
        missing = os.path.join(tmp, "absent.jsonl")
        code, findings, errors = evaluate(missing, brief_path)
        module = sys.modules[__name__]
        module._absent = _absent_is_empty
        try:
            flip = evaluate(missing, brief_path)[0]
        finally:
            module._absent = _real_absent
        check("input/ledger-missing", code == 2 and len(errors) == 1 and "does not exist" in errors[0]
              and flip == 0, (code, errors, flip))
        empty = os.path.join(tmp, "empty.jsonl")
        _write(empty, b"")
        got = evaluate(empty, brief_path)
        check("input/ledger-empty-no-block-ok", got == (0, [], []), got)
        directory = os.path.join(tmp, "dir.jsonl")
        os.mkdir(directory)
        code, findings, errors = evaluate(directory, brief_path)
        check("input/ledger-not-regular", code == 2 and len(errors) == 1
              and "a directory" in errors[0], (code, errors))
        fifo = os.path.join(tmp, "fifo.jsonl")
        os.mkfifo(fifo)
        code, findings, errors = evaluate(fifo, brief_path)
        check("input/ledger-fifo-not-regular", code == 2 and len(errors) == 1
              and "FIFO" in errors[0], (code, errors))
        regular = os.path.join(tmp, "regular.jsonl")
        _write(regular, _ledger([_row("CC-1", A_GATE, A_TEXT)]))
        _write(brief_path, one_brief)
        got = evaluate(regular, brief_path)
        check("input/ledger-regular-ok", got == (0, [], []), got)
        code, findings, errors = evaluate(regular, directory)
        check("input/brief-not-regular", code == 2 and "a directory" in " ".join(errors), (code, errors))
        code, findings, errors = evaluate(regular, missing)
        check("input/brief-missing", code == 2 and "does not exist" in " ".join(errors), (code, errors))
        code, findings, errors = evaluate("carry.jsonl", brief_path)
        check("input/ledger-relative-refused", code == 2 and "not absolute" in " ".join(errors),
              (code, errors))
    code, findings, errors = _case(_ledger(one), one_brief.encode("utf-8") + b"\xff\n")
    check("input/brief-non-utf8", code == 2 and "not UTF-8" in " ".join(errors), (code, errors))
    code, findings, errors = _case(_ledger(one), one_brief.replace("\n", "\r\n"))
    check("input/brief-cr", code == 2 and "carriage return" in " ".join(errors), (code, errors))
    code, findings, errors = _case(_ledger(one).encode("utf-8") + b"\xff\n", one_brief)
    check("input/ledger-non-utf8", code == 2 and "not UTF-8" in " ".join(errors), (code, errors))

    # ---------- brief structure: the author fixes these (exit 1) ----------
    for name, brief, needle in (
            ("brief/carry-outside-block",
             _carry(B_GATE, B_TEXT) + "\n" + _brief([_carry(A_GATE, A_TEXT)]), "outside"),
            ("brief/duplicate-line",
             _brief([_carry(A_GATE, A_TEXT), _carry(A_GATE, A_TEXT)]), "repeated"),
            ("brief/second-heading",
             _brief([_carry(A_GATE, A_TEXT)]) + HEADING + "\n", "second"),
            ("brief/malformed-carry-line", _brief([_carry(A_GATE, A_TEXT), "CARRY leaks"]),
             "malformed")):
        code, findings, errors = _case(_ledger(one), brief)
        check(name, code == 1 and len(findings) == 1 and needle in findings[0] and errors == [],
              (code, findings, errors))
    # A line that ends the block, a second heading and a CARRY line with no space after the colon
    # each keep a CARRY line of an active row out of the block, so that row is also missing.
    b_line = _carry(B_GATE, B_TEXT)
    for name, brief, needles in (
            ("brief/carry-after-block-ended",
             _brief([_carry(A_GATE, A_TEXT)], after="Prose ends the block.\n" + b_line + "\n"),
             ("outside", "missing")),
            ("brief/second-heading-then-carry",
             _brief([_carry(A_GATE, A_TEXT), HEADING, b_line]), ("second", "outside", "missing")),
            ("brief/no-space-after-colon",
             _brief([_carry(A_GATE, A_TEXT), b_line.replace(": ", ":", 1)]),
             ("malformed", "missing"))):
        code, findings, errors = _case(both, brief)
        check(name, code == 1 and errors == [] and len(findings) == len(needles)
              and all(needle in line for needle, line in zip(needles, findings)),
              (code, findings, errors))
    code, findings, errors = _case(_ledger(one), one_brief + "\n" + "Later prose.\n")
    check("brief/one-block-ok", (code, findings, errors) == (0, [], []), (code, findings, errors))

    # ---------- bounds: zero, one and many rows, and a last row retired ----------
    got = [
        _case(_ledger([]), _brief(None))[0],
        _case(_ledger(one), one_brief)[0],
        _case(_ledger(many), _brief([_carry(A_GATE, A_TEXT), _carry(B_GATE, B_TEXT),
                                     _carry("leaks", "")]))[0],
        _case(_ledger(many + [_row("CC-3", "leaks", "", "retired", reason="gate removed")]),
              _brief([_carry(A_GATE, A_TEXT), _carry(B_GATE, B_TEXT)]))[0],
    ]
    check("bound/zero-one-many", got == [0, 0, 0, 0], got)

    # ---------- the command line ----------
    got = [_main_exit(argv) for argv in ([], ["--bogus"], ["--self-test", "--bogus"],
                                         ["--ledger", "/x"], ["--execution-report", "relative/report"])]
    check("cli/unrecognized-argument", got == [2, 2, 2, 2, 2], got)

    if FAILURES:
        print("SELF-TEST FAIL: check_brief_carry {} of {} check(s) failed".format(
            len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_brief_carry {} checks (a dropped, reworded, retired-but-carried, "
          "reactivated-but-dropped or unlisted pair exits 1, each beside its clean control, and the "
          "comparison and status flips turn the refusals green; a retired row with no reason, a "
          "broken chain, a duplicated key, invalid JSON, a seq gap, nesting too deep, a retired "
          "first row, a repeated status, a CR, a blank line, a duplicate active pair, a missing "
          "final LF, an unknown or missing key and an id that changes its pair each exit 2 and a "
          "flip of the rule that refuses it moves it off 2; a bad id, ts, gate, text, status or "
          "reason is the only error of its ledger, exits 2, and a flip of that field's own "
          "predicate alone passes the brief; a missing, non-regular, non-UTF-8 or CR input or a "
          "relative ledger path exits 2; a CARRY line before the block, a repeated line, a second "
          "heading or a malformed CARRY line exits 1 with that one finding; a CARRY line after the "
          "block ended, after a second heading (also reported) or with no space after the colon "
          "exits 1 with that finding and its row reported missing; zero, one and many rows pass "
          "when carried; an unrecognized argument exits 2)".format(len(EXECUTED)))
    return 0


_real_absent = _absent


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(list(argv))


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only
    # in that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    if len(argv) == 3 and argv[0] == "--ledger" and not argv[1].startswith("-") \
            and not argv[2].startswith("-"):
        return run(argv[1], argv[2])
    print("error: unrecognized arguments {!r}; usage: check_brief_carry.py --ledger ABS_LEDGER BRIEF "
          "or --self-test; fail closed".format(argv), file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This tool imports the mistakes-register
# helpers from a module that imports subprocess and runs git, so the coverage scan counts this entry as
# one that reads a child's exit status, and under an ignored SIGCHLD a failing child would read as a
# pass; the guard runs at the entry line, before main() dispatches.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("check_brief_carry.py") or main())
