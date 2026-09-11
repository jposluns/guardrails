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
not a whole number of minutes (outside TOML offset syntax), a timezone-aware `time` (TOML local time
carries no offset), and a cyclic table reference (a table reachable from itself: no parsed TOML is
cyclic, so a cycle cannot round-trip and would otherwise not terminate). Each rejected state is one TOML
cannot round-trip, so its closed-subset boundary keeps the staging proof sound. Inline tables and arrays of inline tables are
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

Output ceiling (OPF staging output policy, not a depth bound): a single emission is bounded by
_MAX_EMIT_BYTES of canonical output. A document of any nesting depth succeeds while its canonical bytes
fit under the ceiling; only an output that would exceed it is a fail-closed EmitError. Canonical headers
repeat their full dotted path, so a deep chain's output is quadratic in its depth even from a small
resident model, and this bounds that output rather than the structure.

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

# The admitted scalar/date-time built-ins. Admission is by EXACT type tested by IDENTITY (via
# _is_scalar_type: `type(v) is T`, never isinstance and never `==`), so a hostile subclass of an admitted
# built-in is rejected as out-of-subset before any of its methods runs, and a hostile metaclass's __eq__ is
# never invoked while classifying (an `in _SCALAR_TYPES` membership test would compare with `==` and run
# it); this is what closes the hostile-subclass exception-leak class.
_SCALAR_TYPES = (str, bool, int, float,
                 datetime.datetime, datetime.date, datetime.time)

# Staging output ceiling (OPF staging resource bound, NOT a depth bound): the total canonical bytes a
# single emission may produce. A document of any nesting depth succeeds while its output fits; only an
# output that would exceed this ceiling is a fail-closed EmitError. Canonical headers repeat their full
# dotted path, so a deep chain's output is quadratic in its depth even from a small resident model, and
# this caps that output, never the structure. Accounting is per appended line (len(utf-8) + 1 for the LF
# that "\n".join adds), exact for the final text. Disclosed residuals: (1) a single line (one header or
# one escaped scalar) is assembled just before it is charged, so peak transient memory can exceed the
# ceiling by roughly one rendered line: a header line, whose length is its full dotted path, or one
# escaped scalar. The scheduler renders each header at append time and no longer pre-materializes one
# header string per array-of-tables element, so a wide array of tables adds no transient spike of K times
# the header length; (2) the empty document's single trailing LF is charged as 0 bytes (immaterial at any
# real ceiling); (3) a MemoryError from a model too large to hold is not caught by the ceiling accounting
# here, but it is an ordinary Exception subclass, so the outermost emit() backstop converts it to a
# fail-closed EmitError like any other non-control-flow BaseException; only the three genuine control-flow
# signals (KeyboardInterrupt, SystemExit, GeneratorExit) are re-raised (honored) rather than converted. Any
# reduction of this ceiling is a maintainer decision.
_MAX_EMIT_BYTES = 64 * 1024 * 1024


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


def _safe_type_label(value):
    """A human label for a value's type for a rejection diagnostic, formed WITHOUT letting attacker code
    escape. A value whose class has a hostile metaclass (one whose __getattribute__ raises on the
    __name__ lookup) would make a bare `type(value).__name__` raise an uncontrolled exception while the
    reject message is being built, even though the exact-type gate has already decided to reject the
    value. Reading the name inside a try/except and falling back to a constant on any BaseException OTHER
    than a genuine control-flow signal (KeyboardInterrupt, SystemExit, GeneratorExit) means forming a
    rejection message can never raise a non-control-flow exception; a genuine control-flow signal is
    re-raised (honored), matching the outermost emit() backstop, which closes the same class definitively."""
    try:
        label = type(value).__name__
    except (KeyboardInterrupt, SystemExit, GeneratorExit):  # a genuine control-flow signal raised during the
        raise  # __name__ lookup is honored (re-raised), matching the emit() backstop, never swallowed to a constant
    except BaseException:  # noqa: BLE001 - any OTHER failure to read the type name (including a non-control-flow
        # BaseException raised by a hostile metaclass during the __name__ lookup) falls back to a constant label.
        # Catching BaseException here is safe: this helper neither loops nor blocks, it is a pure best-effort
        # diagnostic label, and any escape would defeat the diagnostic.
        return "<unrenderable-type>"
    if type(label) is not str:  # a hostile metaclass can return a non-str __name__ whose __format__ raises a
        return "<unrenderable-type>"  # control-flow signal; reject it BEFORE a diagnostic ever formats the label
    return label


def _render_key(key):
    """A single key component: bare where it matches the bare-key grammar, else a quoted basic string.
    A non-string key is outside the subset (tomllib only ever produces string keys, so a non-string key
    could never round-trip)."""
    if type(key) is not str:  # exact type, not isinstance: a str subclass is rejected before it is iterated
        raise EmitError("table key must be a string, got {}".format(_safe_type_label(key)))
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
    """A single scalar or date/time value as its canonical TOML literal. Admission is by EXACT type
    (`type(value) is T`, not isinstance): only a plain admitted built-in is emitted and any subclass is
    rejected as out-of-subset before any of its methods (__str__, __repr__, isoformat) runs, so a hostile
    subclass cannot leak an uncontrolled exception. Exact typing also disambiguates bool from int and
    datetime from date without relying on test order."""
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        # Defence-in-depth for a currently-unreachable-from-TOML input: CPython raises ValueError on
        # str() of an int whose decimal length exceeds the interpreter's integer-string-conversion limit
        # (4300 digits by default). Such an int cannot arrive via tomllib (it rejects an over-limit int
        # literal at parse), so this is unreachable from parsed TOML; the guard is here so the emitter's
        # fail-closed posture never rests on tomllib's limit. An oversized int maps to the module's
        # controlled EmitError, never an uncontrolled ValueError. A normal-magnitude int spells
        # byte-identically, so emitted output is preserved for every real input.
        try:
            return str(value)
        except ValueError as exc:
            raise EmitError("integer is too large to render ({})".format(exc))
    if type(value) is float:
        return _canonical_float(value)
    if type(value) is str:
        return _escape_basic(value)
    if type(value) is datetime.datetime:
        if value.fold:
            raise EmitError("a datetime with fold=1 has no TOML round trip (TOML carries no fold flag)")
        tz = value.tzinfo
        if tz is not None:
            # A TOML offset datetime carries only a numeric UTC offset: no zone name, no DST rule. Accept
            # ONLY a plain datetime.timezone constructed WITHOUT a name; anything else (a custom name, even
            # one that matches the auto-generated "UTC+HH:MM" string, a variable/named zone, or a timezone
            # subclass) drops constructor state that would silently vanish on reparse. Both timezone
            # equality and datetime equality ignore the tzinfo name, so the name can be caught neither by
            # comparing offsets nor by the round-trip proof; reconstruct the canonical unnamed instance for
            # this offset and require the input to render identically to it, which exposes a custom name
            # (it appears in the repr) that an == comparison would miss. The type gate runs BEFORE
            # utcoffset() so a hostile tzinfo subclass whose utcoffset() returns an out-of-range or
            # non-timedelta value is rejected fail-closed here rather than raising outside EmitError; a
            # genuine datetime.timezone's utcoffset() cannot raise once the type gate has passed.
            if type(tz) is not datetime.timezone:
                raise EmitError("a datetime whose tzinfo is not a plain unnamed fixed UTC offset (a named "
                                "or variable zone) has no TOML round trip")
            offset = value.utcoffset()
            if repr(tz) != repr(datetime.timezone(offset)):
                raise EmitError("a datetime whose tzinfo is not a plain unnamed fixed UTC offset (a named "
                                "or variable zone) has no TOML round trip")
            if offset % datetime.timedelta(minutes=1) != datetime.timedelta(0):
                raise EmitError("a datetime UTC offset that is not a whole number of minutes ({}) is "
                                "outside TOML offset syntax".format(offset))
        return value.isoformat()
    if type(value) is datetime.date:
        return value.isoformat()
    if type(value) is datetime.time:
        if value.tzinfo is not None:
            raise EmitError("a TOML local time cannot carry a timezone offset")
        if value.fold:
            raise EmitError("a time with fold=1 has no TOML round trip (TOML carries no fold flag)")
        return value.isoformat()
    raise EmitError("value is outside the subset: {}".format(type(value).__name__))


def _is_scalar_type(value):
    """True iff `value`'s EXACT type is an admitted scalar/date-time built-in, tested by IDENTITY (never
    `==`). Membership via `type(value) in _SCALAR_TYPES` would compare with `==`, running a hostile
    metaclass's __eq__ during classification (which can raise a control-flow signal or fail to terminate);
    an identity test never invokes it, matching the exact-type-by-identity admission the module relies on."""
    t = type(value)
    return any(t is scalar_type for scalar_type in _SCALAR_TYPES)


def _classify_list(items):
    """Classify a list as 'empty', 'scalar' (an inline array of scalars/dates), or 'aot' (an array of
    tables). A mixed or nested array is outside the subset and fails closed."""
    if not items:
        return "empty"
    if all(type(e) is dict for e in items):  # exact type: a dict subclass is not admitted as a table
        return "aot"
    if all(_is_scalar_type(e) for e in items):  # exact type by identity: a scalar subclass is rejected below
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
    tables (`[[header]]`) then follow, each preceded by a blank separator line except at the very top.

    The walk is an explicit-stack pre-order traversal, not native recursion, so an arbitrarily deep
    document emits without a RecursionError and no depth is rejected. The stack carries three tagged
    frame kinds: a `process` frame renders one table body and schedules its blocks; a `block` frame
    emits one header (with the leading blank separator, decided at the append moment from the current
    non-emptiness of `lines`, exactly as the recursion did) and schedules that block's body above the
    remaining siblings; a `leave` frame marks a table's subtree complete. Because each block's body is
    scheduled above its siblings, a subtree finishes before the next sibling begins and the append order
    is byte-identical to the recursive form. A table reached again while still on the active ancestor
    chain is a cyclic reference (which no parsed TOML can contain and which would otherwise not
    terminate) and is a fail-closed EmitError; a table shared acyclically leaves the active chain when
    its subtree completes, so a shared DAG still emits. Emission is bounded by _MAX_EMIT_BYTES: an output
    that would exceed the staging ceiling is a fail-closed EmitError."""
    total = 0

    def _append(line):
        # Charge each appended line as len(utf-8) + 1 for the LF that "\n".join adds for it; this sum is
        # exact for the final text (a single line, scalar, key, or header, is assembled just before it is
        # charged, so peak transient memory can exceed the ceiling by roughly one such line: a disclosed
        # residual).
        nonlocal total
        total += len(line.encode("utf-8")) + 1
        if total > _MAX_EMIT_BYTES:
            raise EmitError("emitted document exceeds the staging output ceiling ({} bytes)".format(
                _MAX_EMIT_BYTES))
        lines.append(line)

    # ("process", table, path): render one table body. ("block", kind, header, table, path): emit one
    # header, built from the shared dotted-path `header` string at the append moment (no per-element header
    # string is pre-materialized), and schedule its body. ("leave", id): the table with this id() has
    # finished; unmark it.
    stack = [("process", table, path)]
    active = set()  # id() of every table currently on the ancestor chain, for cycle detection
    while stack:
        frame = stack.pop()
        tag = frame[0]
        if tag == "leave":
            active.discard(frame[1])
            continue
        if tag == "block":
            _, kind, header, tbl, child_path = frame
            if lines:
                _append("")
            _append(("[[{}]]" if kind == "aot" else "[{}]").format(header))
            stack.append(("process", tbl, child_path))
            continue
        _, tbl, pth = frame  # a "process" frame
        if id(tbl) in active:
            raise EmitError("document contains a cyclic table reference (reached again at [{}])".format(
                ".".join(_render_key(p) for p in pth)))
        active.add(id(tbl))
        stack.append(("leave", id(tbl)))
        leaves = []
        nested = []
        for key, value in tbl.items():
            _render_key(key)  # validate the key up front (raises on a non-string key)
            # Exact-type dispatch (`type(value) is T` / `type(value) in _SCALAR_TYPES`, not isinstance): a
            # hostile subclass of dict/list/an admitted scalar falls through to the out-of-subset branch
            # below and is rejected before its .items()/iteration/render method can run.
            if type(value) is dict:
                nested.append((key, value, "table"))
            elif type(value) is list:
                if _classify_list(value) == "aot":
                    nested.append((key, value, "aot"))
                else:
                    leaves.append((key, value))  # empty or scalar array: an inline leaf
            elif _is_scalar_type(value):
                leaves.append((key, value))
            else:
                raise EmitError("value for key {!r} is outside the subset: {}".format(
                    key, _safe_type_label(value)))
        for key, value in sorted(leaves, key=lambda kv: kv[0]):
            rendered = _render_scalar_array(value) if type(value) is list else _render_scalar(value)
            _append("{} = {}".format(_render_key(key), rendered))
        # Build block frames in the exact order the recursion emitted them (sub-tables and array-of-table
        # elements, sorted by key, elements in positional order), then push them reversed so the LIFO
        # stack pops them back into that forward order. Each frame carries the ONE shared dotted-path
        # `header` string (and the one child_path list), not a per-element formatted header; the bracketed
        # header line is rendered at append time, so a wide array of tables materializes no K header copies.
        blocks = []
        for key, value, kind in sorted(nested, key=lambda t: t[0]):
            child_path = pth + [key]
            header = ".".join(_render_key(p) for p in child_path)
            if kind == "table":
                blocks.append(("block", "table", header, value, child_path))
            else:  # an array of tables: one [[header]] block per element
                for element in value:
                    blocks.append(("block", "aot", header, element, child_path))
        for block in reversed(blocks):
            stack.append(block)


def emit(document):
    """Serialize `document` (a dict) to a canonical, byte-canonical TOML string ending in exactly one
    LF. Fail-closed (EmitError) on anything outside the constrained subset. The result reparses to a
    model equal to `document`; emit_checked() proves that on every emission before it can stage.

    The whole body runs inside a fail-closed backstop: this is the outermost boundary of emit() and it
    closes the hostile-input exception-leak class definitively. An EmitError propagates unchanged; the
    genuine control-flow signals (KeyboardInterrupt, SystemExit, GeneratorExit) are re-raised untouched;
    every OTHER BaseException (for example a value whose hostile metaclass raises a custom Exception OR a
    custom BaseException subclass while a diagnostic is built, or any other pathological input) is
    converted to a fail-closed EmitError with a constant, value-free message, never one that formats or
    introspects the offending value or type. Catching BaseException (not merely Exception) is deliberate:
    a hostile object whose metaclass __getattribute__ raises a BaseException subclass that is NOT an
    Exception subclass would otherwise slip past an `except Exception` backstop and escape uncontrolled.
    This guarantees no hostile or pathological input can escape emit() (and therefore emit_checked, which
    calls emit()) as an uncontrolled exception.

    DISCLOSURE (residual): the sole non-EmitError escape is a GENUINE control-flow signal
    (KeyboardInterrupt, SystemExit, GeneratorExit) raised by the input DURING emission, whether raised
    directly or from an attribute or method the emitter legitimately invokes on a plain-typed value. Such
    a raise is honored as control flow and propagates rather than becoming an EmitError, because it is
    indistinguishable from a real interrupt or exit and must be allowed to propagate; it is never dressed
    up as a document reject. It does not fail open: nothing is staged or returned on that path."""
    try:
        if type(document) is not dict:  # exact type: a dict subclass is rejected before its .items() runs
            raise EmitError("the document must be a table (dict) at top level, got {}".format(
                _safe_type_label(document)))
        lines = []
        _emit_table(document, [], lines)
        return "\n".join(lines) + "\n"
    except EmitError:
        raise
    except (KeyboardInterrupt, SystemExit, GeneratorExit):  # genuine control flow re-raised, never converted
        raise
    except BaseException:  # noqa: BLE001 - fail-closed backstop: any OTHER BaseException becomes a value-free EmitError
        raise EmitError("emit failed on an out-of-subset or hostile input (fail-closed)")


def _model_equal(a, b):
    """Strict structural, type-aware equality between an input model and its reparse. Stricter than ==:
    bool is never equal to a bare int (Python's True == 1 would otherwise mask a bool-vs-int fidelity
    bug), int is never equal to float, and datetime is never equal to date. This is what makes the
    round-trip proof in emit_checked meaningful rather than merely plausible.

    The comparison is an explicit-stack traversal, not native recursion, so an arbitrarily deep pair is
    compared without a RecursionError. Every clause below is applied per pair in the same order the
    recursive form used (bool-symmetric first, then dict, then list, then type, then ==), and the first
    inequality short-circuits to False. An `x is y` identity short-circuit heads the loop: it bounds the
    case where the SAME object is reached as both members of a pair, a shared DAG passed as both arguments
    (which the guardless walk expands exponentially over the shared nodes) or a cyclic object passed as
    both arguments (which it walks without ever terminating, whereas the prior recursive form terminated by
    raising RecursionError). Both cases are unreachable in-module: emit_checked compares the input against a
    fresh tomllib tree (a finite acyclic model, and emit() has already rejected a cyclic input before this
    runs), so no in-module call can loop; the guard is cheap defence-in-depth for a direct external call.
    The guard treats an identical object as equal, which matches == for every value emit admits and never
    changes an in-module result: the reparse is a tree distinct from the input, so a shared container is
    never identical across the two trees, and where an interned scalar (a small int, an interned str, True,
    False) is identical across them it is reflexively equal, so the verdict is unchanged. The one value
    whose identity does not imply == is a NaN (a shared NaN would be treated as equal though == calls it
    unequal), but emit rejects a non-finite float before this function runs, so no NaN can reach it
    in-module. Identity MEMOIZATION of distinct-but-equal pairs would change the equality semantics for no
    in-module caller and is deliberately omitted."""
    stack = [(a, b)]
    while stack:
        x, y = stack.pop()
        if x is y:  # the identical object is equal to itself; bounds a shared-DAG/cyclic both-args walk
            continue
        # Exact-type discrimination (type(...) is T, not isinstance), so a subclass of an admitted built-in
        # cannot slip past here either; the strict semantics are unchanged (bool != bare int, int != float,
        # datetime != date all fall through to the type(x) is not type(y) check below).
        if type(x) is bool or type(y) is bool:
            if not (type(x) is bool and type(y) is bool and x == y):
                return False
            continue
        if type(x) is dict:
            if type(y) is not dict or x.keys() != y.keys():
                return False
            for k in reversed(x):  # push children reversed so they pop in positional (insertion) order
                stack.append((x[k], y[k]))
            continue
        if type(x) is list:
            if type(y) is not list or len(x) != len(y):
                return False
            for i in range(len(x) - 1, -1, -1):  # push children reversed so they pop in positional order
                stack.append((x[i], y[i]))
            continue
        if type(x) is not type(y):
            return False
        if x != y:
            return False
    return True


def emit_checked(document):
    """The staging contract: emit `document`, reparse the result, and confirm it is model-equivalent to
    the input before returning the text. Nothing that does not reparse or does not round-trip is ever
    returned, so a caller (U7 import staging, `opf init`) can stage the text knowing it is faithful.
    The two failure modes are defensive: a correct emitter never reaches them, so either is a fail-closed
    EmitError, never a silent degraded write."""
    text = emit(document)
    try:
        reparsed = tomllib.loads(text)
        if not _model_equal(document, reparsed):
            raise EmitError("emitted document did not round-trip to a model equal to its input; fail-closed")
    except tomllib.TOMLDecodeError as exc:
        raise EmitError("emitted document did not reparse as TOML ({}); fail-closed".format(exc))
    except EmitError:
        raise
    except (KeyboardInterrupt, SystemExit, GeneratorExit):  # genuine control flow re-raised, never converted
        raise
    except BaseException:  # noqa: BLE001 - fail-closed backstop mirroring emit(): a NON-TOMLDecodeError
        # reparse or comparison failure (a RecursionError from a >1000-part dotted key on 3.12/3.13, or a
        # MemoryError building the second tree or the comparison stack) becomes a value-free EmitError, so
        # emit_checked honours the same no-uncontrolled-exception contract as emit() and U7's `except
        # EmitError` fail-closed path is never bypassed by a leaked exception.
        raise EmitError("emitted document could not be reparsed or compared for the round-trip proof; "
                        "fail-closed")
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
    _saved_sys_path = list(sys.path)  # snapshot so the import (and its transitive imports) cannot leak sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        try:
            import check_byte_canon
        except Exception as exc:  # noqa: BLE001 - any import failure is fail-closed here
            print("error: cannot import check_byte_canon for the byte-canon leg ({}); fail-closed".format(exc),
                  file=sys.stderr)
            return 2
    finally:
        sys.path[:] = _saved_sys_path  # restore whether the import succeeded, failed (return 2), or completed; check_byte_canon stays in sys.modules

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

    # --- arbitrary-depth vectors: emission and equality without native recursion -----------------------
    # Two properties that the reparser forces apart onto two depths. ITERATIVENESS: emit() must handle
    # nesting far deeper than the interpreter's recursion limit; the pre-fix recursive emitter raises
    # RecursionError building these, so a depth well above the pinned limit discriminates the rewrite.
    # This depth is emitted but never reparsed: a chain of depth D emits a D-part header (both a table,
    # [t.t...], and an array-of-tables, [[r.r...]]), and tomllib caps a key at 1000 dotted parts, raising
    # RecursionError on the giant key on the Pythons that enforce the cap (3.12/3.13) though not on those
    # that do not (3.14). Handing the giant key to the reparser would misread that cap as native emitter
    # recursion, so iterativeness is checked by emission and structure alone. FIDELITY: the tomllib
    # round-trip runs at a depth under the cap, where every reparser accepts the key. Pin the recursion
    # limit low so the iterativeness depth always exceeds the effective limit regardless of the ambient
    # value (test-hermeticity, and keeping emitted output bounded), and restore it afterwards.
    iterative_depth = 2500      # above the pinned limit; emitted and structurally checked, never reparsed
    reparse_depth = 500         # well under tomllib's 1000-part-key cap; round-tripped through the reparser
    saved_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(min(saved_limit, 2000))
    try:
        # Iterativeness: the recursive emitter raises RecursionError building these; the iterative one
        # does not. Emission must be deterministic and match, byte for byte, an independently constructed
        # canonical expected vector (built here from first principles, never by calling emit), so a
        # regressed emitter that reached full depth but produced garbage, reordered, missing, extra, or
        # non-canonical bytes is rejected without reparsing the >1000-part key. Byte-canon cleanliness is
        # asserted on the emitted text too.
        for wrap, open_tok, close_tok, part, label in (
                (lambda n: {"t": n}, "[", "]", "t", "deep/table"),
                (lambda n: {"r": [n]}, "[[", "]]", "r", "deep/aot")):
            model = {"leaf": 1}
            for _ in range(iterative_depth):
                model = wrap(model)
            expected = "\n\n".join(
                open_tok + ".".join([part] * i) + close_tok for i in range(1, iterative_depth + 1)
            ) + "\nleaf = 1\n"
            text = emit(model)
            if text != expected:
                failures.append("{}: depth-{} emission does not match the canonical expected bytes".format(
                    label, iterative_depth))
            if emit(model) != text:
                failures.append("{}: two emissions of a depth-{} model differ (non-deterministic)".format(
                    label, iterative_depth))
            _byte_canon_clean(text, label + "/deep")

        # Fidelity: at a depth the reparser accepts, the table and array-of-tables forms round-trip
        # canonically, and the array-of-tables reparse preserves the full depth and the leaf value.
        deep_table = {"leaf": 1}
        for _ in range(reparse_depth):
            deep_table = {"t": deep_table}
        _round_trips(deep_table, "deep/table-roundtrip")

        deep_aot = {"leaf": 1}
        for _ in range(reparse_depth):
            deep_aot = {"r": [deep_aot]}
        _round_trips(deep_aot, "deep/aot-roundtrip")
        # Walk the reparse to the bottom iteratively: element order and the leaf value must survive.
        node = tomllib.loads(emit(deep_aot))
        walked = 0
        while isinstance(node, dict) and "r" in node:
            node = node["r"][0]
            walked += 1
        if walked != reparse_depth or not (isinstance(node, dict) and node.get("leaf") == 1):
            failures.append("deep/aot: reparse did not preserve depth {} and the leaf value".format(reparse_depth))

        # Model-equality holds at the full iterativeness depth (_model_equal is iterative): two
        # independently built deep models compare equal, and a mutated deepest leaf compares unequal.
        first = {"leaf": 1}
        for _ in range(iterative_depth):
            first = {"t": first}
        second = {"leaf": 1}
        for _ in range(iterative_depth):
            second = {"t": second}
        if not _model_equal(first, second):
            failures.append("deep/model-equal: two independently built depth-{} models compared unequal".format(
                iterative_depth))
        node = second
        while "t" in node and isinstance(node["t"], dict):
            node = node["t"]
        node["leaf"] = 2
        if _model_equal(first, second):
            failures.append("deep/model-equal: a mutated deepest leaf was not detected as unequal")
    except (RecursionError, EmitError) as exc:
        failures.append("deep/vectors: an arbitrary-depth path failed to emit iteratively ({!r})".format(exc))
    finally:
        sys.setrecursionlimit(saved_limit)

    # --- shared acyclic DAG: the cycle guard must not over-fire on a table with several parents ---------
    shared = {"x": 1}
    _round_trips({"p": shared, "q": shared, "rows": [shared, shared]}, "shared-dag")

    # MINOR-4 identity-guard pin: two DISTINCT lists that each hold the SAME deep shared object collapse
    # under the `x is y` short-circuit, so _model_equal returns True in O(nodes). Each level is a diamond
    # (one child shared under two keys); without the short-circuit the guardless walk expands the diamonds
    # (2**64 pair-pushes) and does not return promptly, so removing the guard is caught here. Hermetic: no
    # timers, no wall-clock, no host state; under the guard this runs in a handful of iterations.
    shared_sub = {"leaf": 1}
    for _ in range(64):
        shared_sub = {"l": shared_sub, "r": shared_sub}
    if not _model_equal([shared_sub, shared_sub], [shared_sub, shared_sub]):
        failures.append("identity-guard/shared-dag: a shared DAG compared unequal to itself")

    # _model_equal type-strictness pins: the exact-type clause (int != float, datetime != date) is the sole
    # carrier of the strictness that makes the round-trip proof meaningful rather than merely plausible. A
    # mutant dropping that clause falls back to bare ==, so 1 would equal 1.0; no other leg asserts
    # _model_equal returns False on a type-conflated pair, so these direct assertions turn that mutant red.
    if _model_equal(1, 1.0):
        failures.append("model-equal/int-vs-float: 1 compared equal to 1.0 (exact-type strictness lost)")
    if _model_equal(datetime.datetime(2026, 1, 1), datetime.date(2026, 1, 1)):
        failures.append("model-equal/datetime-vs-date: a datetime compared equal to a date")
    if _model_equal(True, 1) or _model_equal(1, True):
        failures.append("model-equal/bool-vs-int: True compared equal to a bare int")
    if _model_equal({"n": 1}, {"n": 1.0}):
        failures.append("model-equal/nested-int-vs-float: a nested 1 compared equal to 1.0")

    # Identity-membership pin: scalar admission tests _SCALAR_TYPES by IDENTITY (_is_scalar_type), never
    # `==`, so classifying never invokes a hostile metaclass's __eq__. This spy's __eq__ records every
    # invocation and returns NotImplemented (so membership still resolves False and the value is rejected);
    # a `type(v) in _SCALAR_TYPES` regression would compare with `==` and populate the record, turning this
    # red, while the value stays fail-closed either way.
    _eq_calls = []

    class _EqSpyMeta(type):
        def __eq__(cls, other):
            _eq_calls.append(other)
            return NotImplemented

        def __hash__(cls):
            return id(cls)

    class _EqSpy(metaclass=_EqSpyMeta):
        pass

    _eq_spy = _EqSpy()
    for _label, _doc in (("value", {"k": _eq_spy}), ("aot-element", {"k": [_eq_spy]}),
                         ("scalar-array", {"k": [1, _eq_spy]})):
        _eq_calls.clear()
        if not _rejects(_doc):
            failures.append("identity-membership/{}: a hostile-eq value was accepted".format(_label))
        if _eq_calls:
            failures.append("identity-membership/{}: classifying invoked a metaclass __eq__ ({} times) via "
                            "`==` membership instead of an identity test".format(_label, len(_eq_calls)))

    # --- golden byte vectors: parity locks over sorting, separators, empties, and dotted headers --------
    # The leaf-rooted golden below (built in a deliberately noncanonical insertion order; the literal was
    # captured from this emitter) has root-level leaf key-values, so it exercises the separator-PRESENT
    # case (a blank line before a block that follows leaves). The leafless-rooted golden further down
    # exercises the top-of-document separator-ABSENT case, together pinning the if-lines branch in both its
    # taken and not-taken states.
    golden = (
        'a = [3, 1]\n'
        'b = true\n'
        'm = "x"\n'
        '\n'
        '[empty_table]\n'
        '\n'
        '[[rows]]\n'
        'a = 1\n'
        'z = 9\n'
        '\n'
        '[rows.inner]\n'
        'k = "v"\n'
        '\n'
        '[[rows]]\n'
        'empty = []\n'
        '\n'
        '[z_table]\n'
        'alpha = 1\n'
        'beta = 2\n'
        '\n'
        '[z_table.a_child]\n'
        'q = "x"\n'
    )
    golden_doc = {}
    golden_doc["m"] = "x"
    golden_doc["rows"] = [{"z": 9, "inner": {"k": "v"}, "a": 1}, {"empty": []}]
    golden_doc["b"] = True
    golden_doc["empty_table"] = {}
    golden_doc["a"] = [3, 1]
    golden_doc["z_table"] = {"beta": 2, "a_child": {"q": "x"}, "alpha": 1}
    if emit(golden_doc) != golden:
        failures.append("golden/byte-vector: emit output is not byte-identical to the pinned golden")
    if emit_checked(golden_doc) != emit(golden_doc):
        failures.append("golden/emit-checked-parity: emit_checked text differs from emit text")

    # MINOR-1 pin: an array of tables under a multi-component dotted path renders each [[a.b]] header from
    # the ONE shared dotted-path string at append time. A mutant that mis-orders, mangles, or drops the
    # deferred [[header]] rendering (or loses the shared-header reference) produces different bytes, so
    # this golden fails. No existing byte-pinned vector places an array of tables under a dotted path (the
    # golden and coverage aots are single-component: [[rows]], [[release]]).
    aot_dotted_doc = {"a": {"b": [{"x": 1}, {"y": 2}]}}
    aot_dotted_golden = '[a]\n\n[[a.b]]\nx = 1\n\n[[a.b]]\ny = 2\n'
    if emit(aot_dotted_doc) != aot_dotted_golden:
        failures.append("golden/aot-dotted-path: an array of tables under a dotted path is not "
                        "byte-identical to the pinned golden")
    if emit_checked(aot_dotted_doc) != emit(aot_dotted_doc):
        failures.append("golden/aot-dotted-path/emit-checked-parity: emit_checked text differs from emit")

    # A leafless-root golden: the root table has NO leaf key-values, so the FIRST emitted line is a block
    # header at top-of-document and the if-lines separator branch is exercised in its not-taken (no leading
    # blank) state. An `if True:` mutant of that branch would emit a leading blank line here while the rest
    # of the self-test stays green, so this vector turns that mutant red.
    golden2_doc = {"outer": {"k": 1}}
    golden2 = '[outer]\nk = 1\n'
    if emit(golden2_doc) != golden2:
        failures.append("golden/leafless-root: emit output is not byte-identical to the pinned golden")
    if emit_checked(golden2_doc) != emit(golden2_doc):
        failures.append("golden/leafless-root/emit-checked-parity: emit_checked text differs from emit")

    # --- output ceiling: the exact byte bound fails closed, and the production ceiling is restored ------
    global _MAX_EMIT_BYTES
    budget_doc = {"a": "x"}  # emits exactly 8 bytes: 'a = "x"\n'
    if len(emit(budget_doc).encode("utf-8")) != 8:
        failures.append("budget/premise: the ceiling probe document did not emit 8 bytes")
    saved_ceiling = _MAX_EMIT_BYTES
    try:
        _MAX_EMIT_BYTES = 8
        try:
            if emit(budget_doc) != 'a = "x"\n':
                failures.append("budget/at-ceiling: a document exactly at the ceiling did not emit")
        except EmitError as exc:
            failures.append("budget/at-ceiling: a document exactly at the ceiling was rejected ({})".format(exc))
        _MAX_EMIT_BYTES = 7
        if not _rejects(budget_doc):
            failures.append("budget/over-ceiling: a document one byte over the ceiling was not rejected")
    finally:
        _MAX_EMIT_BYTES = saved_ceiling
    if _MAX_EMIT_BYTES != saved_ceiling:
        failures.append("budget/restore: the production ceiling was not restored")

    # Multibyte budget boundary: the ceiling charges ENCODED bytes, not characters. This line carries a
    # 2-byte character (U+00E9), so a len(line) mutant of the charge (line 270) under-counts and this leg
    # fails. The emitted document 'a = "\u00e9"\n' is 9 bytes but 8 characters; \u00e9 is not escaped
    # (_escape_basic leaves it literal), so the 2-byte char reaches the output line.
    mb_doc = {"a": "\u00e9"}
    if len(emit(mb_doc).encode("utf-8")) != 9:
        failures.append("budget/multibyte-premise: the multibyte probe did not emit 9 bytes")
    saved_ceiling_mb = _MAX_EMIT_BYTES
    try:
        _MAX_EMIT_BYTES = 9
        try:
            emit(mb_doc)
        except EmitError as exc:
            failures.append("budget/multibyte-at-ceiling: a document exactly at the ceiling was "
                            "rejected ({})".format(exc))
        _MAX_EMIT_BYTES = 8
        if not _rejects(mb_doc):
            failures.append("budget/multibyte-over-ceiling: a document one byte over the ceiling was not "
                            "rejected (byte length vs character length)")
    finally:
        _MAX_EMIT_BYTES = saved_ceiling_mb
    if _MAX_EMIT_BYTES != saved_ceiling_mb:
        failures.append("budget/multibyte-restore: the production ceiling was not restored")

    # --- emit_checked reparse/compare backstop: ANY non-control-flow failure fails closed to EmitError --
    # A >1000-part dotted key makes tomllib raise RecursionError (NOT TOMLDecodeError) on 3.12/3.13, and
    # either the reparse or the comparison can raise MemoryError on a large store-derived model; the
    # contract (docstring) and U7's `except EmitError` fail-closed path require these to become EmitError,
    # never escape uncontrolled. Swap the module tomllib for a stub whose loads() raises a
    # non-TOMLDecodeError and assert the conversion; a mutant catching only TOMLDecodeError, or dropping
    # the reparse/equivalence enforcement entirely, lets the raw exception escape or returns unproven text,
    # so this leg turns red. Hermetic and version-independent; rebind via globals() (not a `global`
    # statement, which cannot follow the earlier tomllib reads in this function) and restore in finally.
    class _RaisingReparse:
        TOMLDecodeError = tomllib.TOMLDecodeError

        def loads(self, text):
            raise RecursionError("stubbed non-TOMLDecodeError reparse failure for the backstop pin")

    _saved_tomllib = globals()["tomllib"]
    globals()["tomllib"] = _RaisingReparse()
    try:
        try:
            emit_checked({"a": 1})
            failures.append("emit-checked/reparse-backstop: a non-TOMLDecodeError reparse failure was not "
                            "converted to a fail-closed EmitError (unproven text returned)")
        except EmitError:
            pass
        except BaseException as exc:  # noqa: BLE001 - anything but EmitError here is the fail-open escape
            failures.append("emit-checked/reparse-backstop: a non-TOMLDecodeError reparse failure escaped "
                            "as {!r} instead of a fail-closed EmitError".format(exc))
    finally:
        globals()["tomllib"] = _saved_tomllib

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
        # An OVERSIZED int (built arithmetically; int("9"*4301) cannot be used because CPython refuses
        # int() on an over-limit numeric string). str() of it raises ValueError on default CPython, so the
        # emitter must reject it fail-closed (EmitError), never let an uncontrolled ValueError escape. This
        # is defence-in-depth for a currently-unreachable-from-TOML input (FIX 1); it fails on the pre-fix
        # code (a bare ValueError) and passes after.
        "oversized-int": {"k": 10 ** 4301},
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
    # Cyclic documents: a table reachable from itself does not round-trip (no parsed TOML is cyclic) and
    # would otherwise not terminate; the iterative emitter rejects it fail-closed. The self-referential
    # list is already rejected by _classify_list (a nested array), pinned here so that stays true.
    cyclic_table = {}
    cyclic_table["self"] = cyclic_table
    cyclic_a = {}
    cyclic_b = {"a": cyclic_a}
    cyclic_a["b"] = cyclic_b
    cyclic_aot = {}
    cyclic_aot["r"] = [cyclic_aot]
    cyclic_list = []
    cyclic_list.append(cyclic_list)
    rejects["self-referential-table"] = cyclic_table
    rejects["indirect-cycle"] = cyclic_a
    rejects["self-referential-aot"] = cyclic_aot
    rejects["self-referential-list"] = {"k": cyclic_list}
    # A hostile custom tzinfo subclass whose utcoffset() returns an out-of-range timedelta must reject
    # fail-closed with EmitError, not escape as an uncontrolled ValueError: the emitter's type gate runs
    # before utcoffset() is ever called on the tzinfo, so the out-of-range value is never evaluated.
    class _OutOfRangeTz(datetime.tzinfo):
        def utcoffset(self, dt):
            return datetime.timedelta(hours=24)

        def tzname(self, dt):
            return None

        def dst(self, dt):
            return None

    rejects["hostile-tzinfo-out-of-range-offset"] = {
        "k": datetime.datetime(2026, 1, 1, tzinfo=_OutOfRangeTz())}
    # A hostile SUBCLASS of each admitted built-in whose overridden method raises must be a fail-closed
    # EmitError, never an uncontrolled RuntimeError escape: exact-type admission (type(value) is T, not
    # isinstance) rejects the subclass BEFORE any of its methods (__str__, __repr__, __iter__, isoformat,
    # iteration, .items()) is called. Each vector leaks a RuntimeError on the pre-fix isinstance code and is
    # a clean EmitError after, so it discriminates the exact-type gate. The tzinfo reject above is retained.
    class _HostileInt(int):
        def __str__(self):
            raise RuntimeError("hostile int __str__ must never be reached")

    class _HostileFloat(float):
        def __repr__(self):
            raise RuntimeError("hostile float __repr__ must never be reached")

    class _HostileStr(str):
        def __iter__(self):
            raise RuntimeError("hostile str __iter__ must never be reached")

    class _HostileDatetime(datetime.datetime):
        def isoformat(self, *args, **kwargs):
            raise RuntimeError("hostile datetime isoformat must never be reached")

    class _HostileList(list):
        def __iter__(self):
            raise RuntimeError("hostile list __iter__ must never be reached")

    class _HostileDict(dict):
        def items(self):
            raise RuntimeError("hostile dict .items() must never be reached")

    rejects["hostile-int-subclass"] = {"k": _HostileInt(5)}
    rejects["hostile-float-subclass"] = {"k": _HostileFloat(1.5)}
    rejects["hostile-str-subclass"] = {"k": _HostileStr("x")}
    rejects["hostile-datetime-subclass"] = {"k": _HostileDatetime(2026, 1, 1)}
    rejects["hostile-list-subclass"] = {"k": _HostileList([1, 2])}
    rejects["hostile-dict-subclass"] = {"k": _HostileDict({"a": 1})}

    # A BENIGN, well-behaved subclass of an admitted container/scalar built-in is ALSO out of the subset:
    # admission is by exact type, so ANY subclass is rejected, not only a hostile one (the documented
    # boundary). No overridden method is needed to expose a regression; these pin the exact-type gates that
    # the hostile-subclass vectors above cannot, because those pass via the outermost backstop regardless of
    # where the raise happens. Weakening an exact-type gate to isinstance (at the aot list-element, the
    # table value, the scalar-array element, or the top-level document position) silently ACCEPTS one of
    # these and emits out-of-subset bytes while every hostile vector still passes, so these turn an
    # isinstance regression red.
    class _BenignDict(dict):
        pass

    class _BenignList(list):
        pass

    class _BenignInt(int):
        pass

    class _BenignStr(str):
        pass

    rejects["benign-dict-subclass-value"] = {"k": _BenignDict({"a": 1})}
    rejects["benign-dict-subclass-aot-element"] = {"k": [_BenignDict({"a": 1})]}
    rejects["benign-list-subclass-value"] = {"k": _BenignList([1, 2])}
    rejects["benign-int-subclass-value"] = {"k": _BenignInt(5)}
    rejects["benign-str-subclass-value"] = {"k": _BenignStr("x")}
    rejects["benign-int-subclass-scalar-array-element"] = {"k": [_BenignInt(5)]}
    rejects["benign-dict-subclass-document"] = _BenignDict({"a": 1})

    # A hostile METACLASS whose __getattribute__ raises on the __name__ lookup: a bare type(value).__name__
    # while a rejection diagnostic is built would otherwise leak an uncontrolled RuntimeError out of emit()
    # (and emit_checked) even though the exact-type gate has already decided to reject the value. The guarded
    # diagnostic (_safe_type_label) and the outermost fail-closed emit() backstop each independently turn
    # this into a clean EmitError, as a VALUE, as a KEY, and as the top-level DOCUMENT. This is the third
    # instance of the hostile-input exception-leak class, after the hostile subclasses and hostile tzinfo.
    class _HostileMeta(type):
        def __getattribute__(cls, name):
            if name == "__name__":
                raise RuntimeError("hostile metaclass __name__ lookup must never reach a reject diagnostic")
            return super().__getattribute__(name)

    class _HostileMetaInt(int, metaclass=_HostileMeta):
        pass

    class _HostileMetaStr(str, metaclass=_HostileMeta):
        pass

    class _HostileMetaDict(dict, metaclass=_HostileMeta):
        pass

    rejects["hostile-metaclass-value"] = {"k": _HostileMetaInt(1)}
    rejects["hostile-metaclass-key"] = {_HostileMetaStr("bad"): "x"}
    rejects["hostile-metaclass-document"] = _HostileMetaDict({"a": 1})

    # The NARROWEST instance of the same exception-leak class: a hostile metaclass whose __getattribute__
    # raises a custom BaseException SUBCLASS (deliberately NOT an Exception subclass) on the __name__ lookup.
    # Such a raise slips past an `except Exception` backstop and would escape emit()/emit_checked
    # uncontrolled; the BaseException-catching emit() backstop (and the BaseException-guarded
    # _safe_type_label) convert it to a clean fail-closed EmitError instead. It must be a plain custom
    # BaseException, not KeyboardInterrupt/SystemExit/GeneratorExit, since those genuine control-flow signals
    # are re-raised rather than converted. Asserted rejected with EmitError, not escaping.
    class _HostileEscape(BaseException):
        pass

    class _HostileBaseMeta(type):
        def __getattribute__(cls, name):
            if name == "__name__":
                raise _HostileEscape("hostile metaclass __name__ lookup raises a BaseException subclass")
            return super().__getattribute__(name)

    class _HostileBaseMetaInt(int, metaclass=_HostileBaseMeta):
        pass

    rejects["hostile-metaclass-baseexception-value"] = {"k": _HostileBaseMetaInt(1)}

    # A hostile metaclass whose __name__ lookup returns a NON-STRING object whose __format__ raises a genuine
    # control-flow signal (KeyboardInterrupt): if a rejection diagnostic ever FORMATTED that label (f-string /
    # .format), the attacker's __format__ would run and manufacture the signal, which the emit() backstop
    # re-raises unchanged, so a non-EmitError would escape for a hostile INPUT. _safe_type_label now accepts
    # the label only when it is a plain str and returns the constant fallback otherwise, BEFORE the label is
    # ever formatted, so the hostile __format__ never runs. Asserted rejected with EmitError, not escaping.
    class _HostileFormatName:
        def __format__(self, spec):
            raise KeyboardInterrupt("hostile __format__ on a type label must never run")

    class _HostileNameMeta(type):
        def __getattribute__(cls, name):
            if name == "__name__":
                return _HostileFormatName()
            return super().__getattribute__(name)

    class _HostileNameInt(int, metaclass=_HostileNameMeta):
        pass

    rejects["hostile-metaclass-nonstr-name-value"] = {"k": _HostileNameInt(1)}
    for name, document in rejects.items():
        try:
            if not _rejects(document):
                failures.append("reject/{}: was accepted but is outside the subset".format(name))
        except Exception as exc:  # noqa: BLE001 - a non-EmitError is a fail-open escape, a defect
            failures.append("reject/{}: raised {!r} instead of a fail-closed EmitError".format(name, exc))

    # HONORED CONTROL-FLOW residual: a hostile metaclass whose __getattribute__ raises a GENUINE control-flow
    # signal (KeyboardInterrupt) on the __name__ lookup. Unlike every hostile-input vector above (each a
    # fail-closed EmitError), a genuine control-flow signal is HONORED: _safe_type_label re-raises it and the
    # emit() backstop re-raises it, so it PROPAGATES unchanged out of emit() and emit_checked() rather than
    # becoming an EmitError, and no document is returned. This pins the disclosed honored-control-flow residual:
    # were the signal swallowed into an EmitError (or any document returned), this leg would fail.
    class _HonoredSignalMeta(type):
        def __getattribute__(cls, name):
            if name == "__name__":
                raise KeyboardInterrupt("a genuine control-flow signal raised during the __name__ lookup")
            return super().__getattribute__(name)

    class _HonoredSignalInt(int, metaclass=_HonoredSignalMeta):
        pass

    honored_doc = {"k": _HonoredSignalInt(1)}
    for fn_name, fn in (("emit", emit), ("emit_checked", emit_checked)):
        try:
            returned = fn(honored_doc)
        except KeyboardInterrupt:
            pass  # honored: the genuine control-flow signal propagated unchanged, as required
        except EmitError as exc:
            failures.append("honored-control-flow/{}: a genuine KeyboardInterrupt was converted to EmitError "
                            "({}) instead of propagating".format(fn_name, exc))
        except BaseException as exc:  # noqa: BLE001 - any other exception is a defect, not the honored signal
            failures.append("honored-control-flow/{}: raised {!r} instead of propagating the KeyboardInterrupt"
                            .format(fn_name, exc))
        else:
            failures.append("honored-control-flow/{}: returned a document ({!r}) instead of propagating the "
                            "KeyboardInterrupt".format(fn_name, returned))

    # The table-cycle rejects must be caught by the active-chain cycle check specifically, not by the
    # output ceiling as a backstop: assert each raises an EmitError that NAMES a cyclic reference. With the
    # cycle check at line 294 removed, these instead grow the dotted header until _MAX_EMIT_BYTES raises a
    # different (ceiling) message, so this leg turns red, distinguishing cycle detection from budget
    # exhaustion. self-referential-list is excluded on purpose: it is rejected by _classify_list as a
    # nested array, so its message legitimately does not name a cycle. Each leg terminates: the cycle check
    # rejects at once, and even the neutralized-mutant path exhausts the 64 MiB ceiling in bounded work.
    for name, document in (("self-referential-table", cyclic_table),
                           ("indirect-cycle", cyclic_a),
                           ("self-referential-aot", cyclic_aot)):
        try:
            emit(document)
            failures.append("cycle-message/{}: a cyclic document was not rejected".format(name))
        except EmitError as exc:
            if "cyclic" not in str(exc):
                failures.append("cycle-message/{}: rejected but the message does not name a cyclic "
                                "reference ({})".format(name, exc))

    # The oversized-int guard is pinned by its SPECIFIC message, not merely by EmitError-rejection: with
    # the guard removed, the escaping ValueError is caught by the outermost emit() backstop and reported
    # with the generic value-free message, so a bare-EmitError assertion cannot tell the guard from the
    # backstop. Asserting the guard's own wording turns a guard-removal mutant red, matching the
    # cycle-message pin pattern above.
    try:
        emit({"k": 10 ** 4301})
        failures.append("oversized-int-message: an oversized int was not rejected")
    except EmitError as exc:
        if "too large to render" not in str(exc):
            failures.append("oversized-int-message: rejected, but not by the specific oversized-int guard "
                            "({})".format(exc))

    # CLI mode selection validates the WHOLE argument vector, not mere membership: exactly one recognized
    # flag runs the self-test, and anything else (an unknown or extra token, a duplicated flag, a bare
    # positional, or an empty vector) is misuse. A membership regression (`"--self-test" in args`) would
    # let a malformed control vector read as a valid self-test run, so these turn that regression red.
    for good_argv in (["--self-test"], ["--selftest"]):
        if _selected_mode(good_argv) != "self-test":
            failures.append("cli/valid: {!r} was not recognized as a self-test invocation".format(good_argv))
    for bad_argv in ([], ["--self-test", "--unknown"], ["--self-test", "--self-test"],
                     ["--selftest", "extra"], ["positional"], ["--self-test", "--selftest"]):
        if _selected_mode(bad_argv) != "misuse":
            failures.append("cli/misuse: {!r} was not classified as misuse (membership, not whole-vector, "
                            "validation)".format(bad_argv))

    # emit_checked returns exactly what emit returns for a good document (no divergent second path).
    if emit_checked(coverage) != emit(coverage):
        failures.append("emit_checked/parity: emit_checked text differs from emit text")

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: round-trip fuzz over adversarial bodies, canonical-form determinism, the "
          "constrained-subset accepted and rejected shapes, byte-canon cleanliness (verified against "
          "check_byte_canon), arbitrary-depth iterative emission and equality (depth {}), cyclic-"
          "reference rejection, shared-DAG acceptance, the output-ceiling bound, and the golden byte "
          "vector all hold".format(iterative_depth))
    return 0


def _selected_mode(args):
    """Map a CLI argument vector to a mode. The WHOLE vector is validated, not mere membership: exactly one
    recognized self-test flag selects 'self-test', and any other vector (an unknown or extra argument, a
    duplicated flag, a bare positional, or an empty vector) is 'misuse', so a malformed control vector is
    never silently read as a valid self-test invocation."""
    if args in (["--self-test"], ["--selftest"]):
        return "self-test"
    return "misuse"


def main():
    if _selected_mode(sys.argv[1:]) == "self-test":
        return self_test()
    print("usage: _opf_emit.py --self-test (a library module; no live mode)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
