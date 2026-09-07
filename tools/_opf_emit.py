#!/usr/bin/env python3
"""OPF unit U8: a constrained-subset deterministic new-document TOML emitter. Stdlib only, fail-closed.

This is the DevProcess (OPF-SPEC.md) new-document writer: it serializes a Python model (a dict) to a
canonical, byte-reproducible TOML string, used ONLY where no TOML preimage exists (import staging under
`.working/toml/imports/<run-id>/`, and any `opf init` scaffolding). It is NOT a round-trip editor of
existing TOML (that writer stays held behind a licence-review dossier, build-plan U8 / fable H4-B3);
editing an existing document in place is out of scope here.

It is distinct from tools/opf_render.py, which renders human-readable MD VIEWS from machine TOML. This
module writes the machine TOML itself.

The constrained subset (closed by construction; anything else is EmitError, fail-closed):
  - scalars: str, int, bool, float (finite only; a non-finite float has no model-equivalent round trip)
  - dates:   datetime.datetime (offset or local; fold=0, and an offset must be a plain fixed UTC offset
             that is a whole number of minutes), datetime.date, datetime.time (local only, fold=0)
  - tables:  a dict, emitted as a `[header]` section (nested dicts nest the header path)
  - arrays of tables: a list whose every element is a dict, emitted as `[[header]]` blocks
  - arrays of scalars: a list whose every element is a scalar/date, emitted inline as `[a, b, c]`
  - an empty list is `[]`; an empty dict is a bare `[header]` (an empty table)
Out of the subset (rejected): None, bytes, set, a nested array (list in a list), an array mixing tables
and scalars, an inline table or an array of inline tables, a non-string key, a non-finite float, a string
(value or key) carrying a lone surrogate (no UTF-8 encoding), a datetime or time with fold=1, a datetime
whose tzinfo is not a plain fixed UTC offset (a named or variable zone), a datetime whose UTC offset is
not a whole number of minutes (outside TOML offset syntax), and a timezone-aware `time` (TOML local time
carries no offset). Each rejected state is one TOML cannot round-trip, so its closed-subset boundary keeps
the staging proof sound. Inline tables and arrays of inline tables are
deliberately excluded: the record-envelope `links`/`refs` inline-table arrays (OPF-SPEC 8.3/8.6) are
outside this minimal subset, matching the build plan's stated U8 coverage.

Determinism (OPF-SPEC 10.3): output is UTF-8, LF-only, with keys ordered canonically (leaf key-values
first, both groups sorted, so a table's rendering is independent of its dict insertion order), so
re-emitting a model-equal input yields byte-identical output. Signed zero is canonicalized on output
(-0.0 emits as 0.0), so the two model-equal float inputs 0.0 and -0.0 emit byte-identically. No
wall-clock content, no network, no model involvement.

Byte-canon (check_byte_canon.py, VER-CORE 3.1): the output is byte-canonical by construction. Every
string value and quoted key is rendered as an escaped basic string, so a body carrying a carriage
return, a trailing space, a control character, or a zero-width / bidirectional control codepoint is
represented by an escape sequence and never reaches the file bytes literally, where it would trip the
byte-canon gate. This is why a value that contains newlines is emitted as an escaped single-line basic
string rather than a literal `\"\"\"` multi-line string: a literal multi-line string cannot satisfy
byte-canon for an arbitrary captured body (a CRLF, a line with trailing whitespace, or a forbidden
codepoint would land in the bytes verbatim). The subset supports strings of any content, including
newlines; the canonical FORM of that support is escaping. check_byte_canon is the authority for the
byte rules; the self-test reconciles this module's forbidden-codepoint set against it and runs every
emitted vector through check_byte_canon.scan_bytes, so the two cannot drift. The en dash (U+2013) and em
dash (U+2014) escaping is a SEPARATE house-style no-dash guarantee, not part of the byte_canon forbidden
set, so the emitted bytes are dash-free by construction independent of what byte_canon covers.

Staging contract (OPF-SPEC 14.1, build plan U8): emit_checked() emits, reparses, and proves the result
model-equivalent to its input before returning it; nothing that does not round-trip can be staged.

  _opf_emit.py --self-test    round-trip fuzz, canonical-form determinism, subset coverage, byte-canon

The live leg is folded into `tools/opf.py --self-test` (build plan section 3); this module is a library
consumed by U7 (import) and `opf init`, with no live/standalone mode beyond the self-test.

Exit convention (matches the repo's gates): 0 clean, 1 a self-test finding, 2 misuse.
"""
import datetime
import math
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: the OPF emitter requires Python 3.11+ (tomllib).")


class EmitError(Exception):
    """A value, key, or structure outside the constrained subset, or an output that fails to round-trip.
    Every path that raises it is fail-closed: no non-canonical or non-round-tripping bytes are returned."""


# A TOML bare key: one or more of A-Za-z0-9_-. Anything else (a dot, a space, an empty key, unicode) is
# rendered as a quoted basic-string key instead.
_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")

# Codepoints the byte-canon gate forbids anywhere in a released text file (check_byte_canon.py 3.1):
# zero-width (U+200B..U+200D, U+2060 word joiner, U+FEFF) and the Unicode Bidi_Control set. They are
# escaped in emitted strings so they never appear literally. This constant is the authority's set; the
# self-test asserts it equals check_byte_canon.FORBIDDEN, so a change there cannot silently pass here.
_ZERO_WIDTH = frozenset(chr(c) for c in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
_BIDI = frozenset(chr(c) for c in (0x061C, 0x200E, 0x200F)
                  + tuple(range(0x202A, 0x202F)) + tuple(range(0x2066, 0x206A)))
_FORBIDDEN_CODEPOINTS = _ZERO_WIDTH | _BIDI

# House-style no-dash guarantee, separate from byte_canon (which does not cover dashes): the en dash
# (U+2013) and em dash (U+2014) are escaped so they never reach the emitted bytes literally, whatever
# the input carries, so the emitted document always satisfies the repo's no-dash convention.
_HOUSE_STYLE_DASHES = frozenset((chr(0x2013), chr(0x2014)))

# The named single-character escapes TOML defines for a basic string; all other required escapes go
# through \uXXXX. \\ and " must be escaped for the string to close correctly.
_NAMED_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t",
                  "\n": "\\n", "\f": "\\f", "\r": "\\r"}

_SCALAR_TYPES = (str, bool, int, float,
                 datetime.datetime, datetime.date, datetime.time)


def _escape_basic(s):
    """Render `s` as a TOML basic string (with surrounding quotes), escaping every character that must
    not appear literally: the two structural characters (\\ and \"), the C0 controls and DEL and the C1
    controls, and the byte-canon forbidden codepoints. The en dash (U+2013) and em dash (U+2014) are also
    escaped, so the emitted bytes are dash-free whatever the input carries; that is a house-style no-dash
    guarantee, separate from byte_canon (which does not cover dashes). Everything else, including ordinary
    printable unicode, is left literal. A lone surrogate has no UTF-8 encoding, so it is rejected
    fail-closed (EmitError) rather than returned in a document whose bytes cannot be encoded. The result
    contains no raw CR, no forbidden codepoint, no dash byte, and (because the closing quote follows the
    content) never leaves a line with trailing whitespace."""
    out = []
    for ch in s:
        o = ord(ch)
        if 0xD800 <= o <= 0xDFFF:
            raise EmitError("string contains a lone surrogate (U+{:04X}) with no UTF-8 encoding".format(o))
        if ch in _NAMED_ESCAPES:
            out.append(_NAMED_ESCAPES[ch])
        elif o < 0x20 or 0x7F <= o <= 0x9F or ch in _FORBIDDEN_CODEPOINTS or ch in _HOUSE_STYLE_DASHES:
            out.append("\\u{:04X}".format(o))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _render_key(key):
    """A single key component: bare where it matches the bare-key grammar, else a quoted basic string.
    A non-string key is outside the subset (tomllib only ever produces string keys, so a non-string key
    could never round-trip)."""
    if not isinstance(key, str):
        raise EmitError("table key must be a string, got {}".format(type(key).__name__))
    if _BARE_KEY_RE.fullmatch(key):
        return key
    return _escape_basic(key)


def _canonical_float(value):
    """The canonical TOML spelling of a float: a non-finite value fails closed (no model-equivalent TOML
    round trip), signed zero is canonicalized (-0.0 spells as 0.0 so model-equal floats spell identically),
    and the value is spelled with repr (which always carries a '.' or 'e', so it is a TOML float). Shared
    with the U3 coverage-digest canonicalization so the emitter and the digest scheme agree byte-for-byte
    on floats (M2)."""
    if not math.isfinite(value):
        raise EmitError("non-finite float ({!r}) has no model-equivalent TOML round trip".format(value))
    if value == 0.0:
        value = 0.0  # canonicalize signed zero: -0.0 emits as 0.0 so model-equal floats emit identically
    return repr(value)


def _render_scalar(value):
    """A single scalar or date/time value as its canonical TOML literal. bool is tested before int
    (bool is an int subclass) and datetime before date (datetime is a date subclass)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, str):
        return _escape_basic(value)
    if isinstance(value, datetime.datetime):
        if value.fold:
            raise EmitError("a datetime with fold=1 has no TOML round trip (TOML carries no fold flag)")
        tz = value.tzinfo
        if tz is not None:
            offset = value.utcoffset()
            # A TOML offset datetime carries only a numeric UTC offset: no zone name, no DST rule. Accept
            # ONLY a plain datetime.timezone constructed WITHOUT a name; anything else (a custom name, even
            # one that matches the auto-generated "UTC+HH:MM" string, a variable/named zone, or a timezone
            # subclass) drops constructor state that would silently vanish on reparse. Both timezone
            # equality and datetime equality ignore the tzinfo name, so the name can be caught neither by
            # comparing offsets nor by the round-trip proof; reconstruct the canonical unnamed instance for
            # this offset and require the input to render identically to it, which exposes a custom name
            # (it appears in the repr) that an == comparison would miss.
            if type(tz) is not datetime.timezone or repr(tz) != repr(datetime.timezone(offset)):
                raise EmitError("a datetime whose tzinfo is not a plain unnamed fixed UTC offset (a named "
                                "or variable zone) has no TOML round trip")
            if offset % datetime.timedelta(minutes=1) != datetime.timedelta(0):
                raise EmitError("a datetime UTC offset that is not a whole number of minutes ({}) is "
                                "outside TOML offset syntax".format(offset))
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.time):
        if value.tzinfo is not None:
            raise EmitError("a TOML local time cannot carry a timezone offset")
        if value.fold:
            raise EmitError("a time with fold=1 has no TOML round trip (TOML carries no fold flag)")
        return value.isoformat()
    raise EmitError("value is outside the subset: {}".format(type(value).__name__))


def _classify_list(items):
    """Classify a list as 'empty', 'scalar' (an inline array of scalars/dates), or 'aot' (an array of
    tables). A mixed or nested array is outside the subset and fails closed."""
    if not items:
        return "empty"
    if all(isinstance(e, dict) for e in items):
        return "aot"
    if all(isinstance(e, _SCALAR_TYPES) for e in items):
        return "scalar"
    raise EmitError("an array must be all tables or all scalars; a mixed or nested array is outside "
                    "the subset")


def _render_scalar_array(items):
    """An inline array of scalars/dates: `[a, b, c]`, or `[]` when empty."""
    return "[" + ", ".join(_render_scalar(e) for e in items) + "]"


def _emit_table(table, path, lines):
    """Append the canonical rendering of `table` (a dict at header `path`) to `lines`. Leaf key-values
    (scalars, dates, empty lists, and scalar arrays) are emitted first, both leaf and nested groups
    sorted by key, so a table's bytes do not depend on its dict insertion order and TOML's rule that a
    table's key-values precede any sub-header is always satisfied. Sub-tables (`[header]`) and arrays of
    tables (`[[header]]`) then recurse, each preceded by a blank separator line except at the very top."""
    leaves = []
    nested = []
    for key, value in table.items():
        _render_key(key)  # validate the key up front (raises on a non-string key)
        if isinstance(value, dict):
            nested.append((key, value, "table"))
        elif isinstance(value, list):
            if _classify_list(value) == "aot":
                nested.append((key, value, "aot"))
            else:
                leaves.append((key, value))  # empty or scalar array: an inline leaf
        elif isinstance(value, _SCALAR_TYPES):
            leaves.append((key, value))
        else:
            raise EmitError("value for key {!r} is outside the subset: {}".format(
                key, type(value).__name__))
    for key, value in sorted(leaves, key=lambda kv: kv[0]):
        rendered = _render_scalar_array(value) if isinstance(value, list) else _render_scalar(value)
        lines.append("{} = {}".format(_render_key(key), rendered))
    for key, value, kind in sorted(nested, key=lambda t: t[0]):
        child_path = path + [key]
        header = ".".join(_render_key(p) for p in child_path)
        if kind == "table":
            if lines:
                lines.append("")
            lines.append("[{}]".format(header))
            _emit_table(value, child_path, lines)
        else:  # an array of tables: one [[header]] block per element
            for element in value:
                if lines:
                    lines.append("")
                lines.append("[[{}]]".format(header))
                _emit_table(element, child_path, lines)


def emit(document):
    """Serialize `document` (a dict) to a canonical, byte-canonical TOML string ending in exactly one
    LF. Fail-closed (EmitError) on anything outside the constrained subset. The result reparses to a
    model equal to `document`; emit_checked() proves that on every emission before it can stage."""
    if not isinstance(document, dict):
        raise EmitError("the document must be a table (dict) at top level, got {}".format(
            type(document).__name__))
    lines = []
    _emit_table(document, [], lines)
    return "\n".join(lines) + "\n"


def _model_equal(a, b):
    """Strict structural, type-aware equality between an input model and its reparse. Stricter than ==:
    bool is never equal to a bare int (Python's True == 1 would otherwise mask a bool-vs-int fidelity
    bug), int is never equal to float, and datetime is never equal to date. This is what makes the
    round-trip proof in emit_checked meaningful rather than merely plausible."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict):
        if not isinstance(b, dict) or a.keys() != b.keys():
            return False
        return all(_model_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            return False
        return all(_model_equal(x, y) for x, y in zip(a, b))
    if type(a) is not type(b):
        return False
    return a == b


def emit_checked(document):
    """The staging contract: emit `document`, reparse the result, and confirm it is model-equivalent to
    the input before returning the text. Nothing that does not reparse or does not round-trip is ever
    returned, so a caller (U7 import staging, `opf init`) can stage the text knowing it is faithful.
    The two failure modes are defensive: a correct emitter never reaches them, so either is a fail-closed
    EmitError, never a silent degraded write."""
    text = emit(document)
    try:
        reparsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise EmitError("emitted document did not reparse as TOML ({}); fail-closed".format(exc))
    if not _model_equal(document, reparsed):
        raise EmitError("emitted document did not round-trip to a model equal to its input; fail-closed")
    return text


# --- self-test --------------------------------------------------------------------------------------

def _rejects(document):
    """True iff emit() rejects `document` with EmitError (the fail-closed subset boundary). Any other
    exception is a defect and is surfaced by the caller as a non-rejection."""
    try:
        emit(document)
        return False
    except EmitError:
        return True


def self_test():
    """Round-trip fuzz over adversarial bodies, canonical-form determinism, constrained-subset coverage
    (accepted and rejected), and byte-canon cleanliness verified against check_byte_canon itself."""
    failures = []

    # check_byte_canon is the authority for the byte rules; reuse it rather than re-implement (a stale
    # duplicate is the guard-input-soundness failure this avoids). Fail closed if it cannot be imported:
    # byte-canon cleanliness cannot be asserted without the authority.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import check_byte_canon
    except Exception as exc:  # noqa: BLE001 - any import failure is fail-closed here
        print("error: cannot import check_byte_canon for the byte-canon leg ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2

    # The forbidden-codepoint set MUST match the authority's, so a body carrying any of them is escaped.
    authority = set(check_byte_canon.FORBIDDEN.values())
    if set(_FORBIDDEN_CODEPOINTS) != authority:
        failures.append("forbidden-codepoint set disagrees with check_byte_canon.FORBIDDEN "
                        "(missing {}, extra {})".format(sorted(authority - set(_FORBIDDEN_CODEPOINTS)),
                                                        sorted(set(_FORBIDDEN_CODEPOINTS) - authority)))

    def _byte_canon_clean(text, label):
        findings = check_byte_canon.scan_bytes(text.encode("utf-8"))
        if findings:
            failures.append("{}: emitted bytes are not byte-canonical: {}".format(label, findings))

    def _round_trips(document, label):
        try:
            text = emit_checked(document)
        except EmitError as exc:
            failures.append("{}: emit_checked rejected a valid document ({})".format(label, exc))
            return
        _byte_canon_clean(text, label)
        # Determinism: a second emit is byte-identical, and re-emitting from the reparsed model is too
        # (canonical form is independent of the model's construction, not merely stable per call).
        if emit(document) != text:
            failures.append("{}: two emissions differ (non-deterministic)".format(label))
        reparsed = tomllib.loads(text)
        if emit(reparsed) != text:
            failures.append("{}: re-emitting the reparsed model is not byte-identical (not canonical)".format(label))

    # --- round-trip fuzz over adversarial string bodies ---------------------------------------------
    fuzz = {
        "triple-quote": 'a"""b"c\\d',
        "backslashes": "C:\\path\\to\\file and a raw \x00 nul",
        "control-chars": "".join(chr(c) for c in range(0x00, 0x20)) + "\x7f",
        "crlf": "line1\r\nline2\r\nline3",
        "lf-only": "one\ntwo\nthree",
        "trailing-space-line": "value with a trailing space \nand more",
        "unicode-and-forbidden": ("caf\u00e9 na\u00efve \u2764 " + chr(0x200B) + chr(0x202E)
                                  + chr(0x2060) + chr(0xFEFF) + " \U0001F600 done"),
        "valid-toml-body": 'schema = 1\n[table]\nkey = "value"\n[[array]]\nx = 1\n',
        "fenced-block": "```python\nprint('hi')\n```\ntext after fence",
        "captured-trace": ("Traceback (most recent call last):\r\n  File \"x.py\", line 3\r\n"
                           "    boom()\r\nRuntimeError: boom\t(with a tab)"),
        "empty-string": "",
        "quotes-and-equals": 'a = "b" # not a comment, just a body',
    }
    for name, body in fuzz.items():
        # As a top-level leaf, nested in a table, and as a field inside an array of tables, so the body
        # survives every structural context the emitter renders.
        _round_trips({"body": body}, "fuzz/{}/leaf".format(name))
        _round_trips({"outer": {"body": body}}, "fuzz/{}/table".format(name))
        _round_trips({"rows": [{"body": body}, {"other": 1}]}, "fuzz/{}/aot".format(name))
        # Confirm fidelity explicitly: the reparsed value is byte-for-byte the original body.
        got = tomllib.loads(emit({"body": body}))["body"]
        if got != body:
            failures.append("fuzz/{}: body did not round-trip faithfully".format(name))

    # A forbidden / awkward codepoint in a KEY is quoted and escaped, and still round-trips.
    _round_trips({"a" + chr(0x200B) + "b": "x", "BACKLOG.md": "y", "with space": "z", "": "empty-key"},
                 "fuzz/keys")

    # --- constrained-subset coverage: every accepted shape --------------------------------------------
    coverage = {
        "schema": 1,
        "big_int": 10 ** 30,
        "neg_int": -7,
        "ratio": 0.5,
        "exp_float": 1.5e-9,
        "flag_true": True,
        "flag_false": False,
        "name": "caf\u00e9",  # ordinary printable unicode stays literal
        "empty_string": "",
        "when_utc": datetime.datetime(2026, 6, 14, 0, 0, 0, tzinfo=datetime.timezone.utc),
        "when_offset": datetime.datetime(2026, 6, 14, 9, 30, 0,
                                         tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30))),
        "when_local": datetime.datetime(2026, 6, 14, 9, 30, 0),
        "day": datetime.date(2026, 6, 14),
        "clock": datetime.time(9, 14, 2),
        "tags": ["a", "b", "c"],
        "spans": ["WL-1", "WL-88"],
        "nums": [1, 2, 3],
        "empty_array": [],
        "store": {"sync_target": ""},
        "empty_table": {},
        "views": {"BACKLOG.md": {"kind": "composed", "sources": ["backlog_item", "block"]}},
        "release": [
            {"version": "1.2.3", "worklog_span": ["WL-1", "WL-88"]},
            {"version": "1.3.0", "worklog_span": [], "meta": {"note": "nested table under an aot element"}},
        ],
    }
    _round_trips(coverage, "coverage/all-shapes")
    # A mixed-type scalar array is valid TOML 1.0 and round-trips, so it is inside the subset (only a
    # nested array, or an array mixing tables with scalars, is out).
    _round_trips({"mixed": [1, "two", True, 3.5]}, "coverage/mixed-scalar-array")
    if emit({}) != "\n":
        failures.append("coverage/empty-document: an empty document is not a single newline")
    _byte_canon_clean(emit({}), "coverage/empty-document")

    # House-style no-dash guarantee: an en/em dash in a value is escaped, so the emitted BYTES are
    # dash-free while the body still round-trips faithfully to the original dash characters.
    dashes = "en {} em {}".format(chr(0x2013), chr(0x2014))
    _round_trips({"body": dashes}, "house-style/dashes")
    dash_text = emit({"body": dashes})
    if chr(0x2013) in dash_text or chr(0x2014) in dash_text:
        failures.append("house-style/dashes: an en/em dash reached the emitted bytes literally")
    if tomllib.loads(dash_text)["body"] != dashes:
        failures.append("house-style/dashes: the dash body did not round-trip faithfully")

    # Signed-zero canonicalization: the two model-equal float inputs 0.0 and -0.0 emit byte-identically
    # (-0.0 normalizes to 0.0), and -0.0 still round-trips (it is model-equal to its 0.0 reparse).
    if emit({"v": -0.0}) != emit({"v": 0.0}):
        failures.append("signed-zero: -0.0 and 0.0 do not emit byte-identically")
    if "-0.0" in emit({"v": -0.0}):
        failures.append("signed-zero: -0.0 emitted a signed-zero literal instead of 0.0")
    _round_trips({"v": -0.0}, "signed-zero/negative")

    # --- constrained-subset coverage: every rejected shape (fail-closed) ------------------------------
    class _Other:
        pass
    rejects = {
        "none-value": {"k": None},
        "bytes-value": {"k": b"bytes"},
        "set-value": {"k": {1, 2, 3}},
        "object-value": {"k": _Other()},
        "float-nan": {"k": float("nan")},
        "float-inf": {"k": float("inf")},
        "float-neg-inf": {"k": float("-inf")},
        "nested-array": {"k": [[1, 2], [3, 4]]},
        "array-tables-and-scalars": {"k": [{"a": 1}, 2]},
        "non-string-key": {1: "x"},
        "nested-non-string-key": {"t": {2: "x"}},
        "tz-aware-time": {"k": datetime.time(9, 0, 0, tzinfo=datetime.timezone.utc)},
        "lone-surrogate-value": {"k": "a" + chr(0xD800) + "b"},
        "lone-surrogate-key": {"a" + chr(0xDC00) + "b": "x"},
        "datetime-fold": {"k": datetime.datetime(2026, 1, 1, 0, 0, 0, fold=1)},
        "time-fold": {"k": datetime.time(9, 0, 0, fold=1)},
        "named-offset-tz": {"k": datetime.datetime(
            2026, 1, 1, 0, 0, 0,
            tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST"))},
        # A NAMED fixed offset whose name equals the canonical "UTC+HH:MM" string is still rejected: the
        # name is explicit constructor state that reparse loses, and both timezone == and datetime == ignore
        # it, so only the emit-time name check catches it (the "IST" case above covers a noncanonical name).
        "canonical-named-offset-tz": {"k": datetime.datetime(
            2026, 1, 1, 0, 0, 0,
            tzinfo=datetime.timezone(datetime.timedelta(hours=1), "UTC+01:00"))},
        "sub-minute-offset": {"k": datetime.datetime(
            2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(seconds=1)))},
        "non-dict-top-level-list": ["not", "a", "table"],
        "non-dict-top-level-scalar": "just a string",
    }
    for name, document in rejects.items():
        try:
            if not _rejects(document):
                failures.append("reject/{}: was accepted but is outside the subset".format(name))
        except Exception as exc:  # noqa: BLE001 - a non-EmitError is a fail-open escape, a defect
            failures.append("reject/{}: raised {!r} instead of a fail-closed EmitError".format(name, exc))

    # emit_checked returns exactly what emit returns for a good document (no divergent second path).
    if emit_checked(coverage) != emit(coverage):
        failures.append("emit_checked/parity: emit_checked text differs from emit text")

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: round-trip fuzz over adversarial bodies, canonical-form determinism, the "
          "constrained-subset accepted and rejected shapes, and byte-canon cleanliness (verified "
          "against check_byte_canon) all hold")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_emit.py --self-test (a library module; no live mode)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
