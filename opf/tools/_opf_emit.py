#!/usr/bin/env python3
"""OPF unit U8: a constrained-subset deterministic new-document TOML emitter. Stdlib only, fail-closed.

This is the OPFiles (OPF-SPEC.md) new-document writer: it serializes a Python model (a dict) to a
canonical, byte-reproducible TOML string, used ONLY where no TOML preimage exists (import staging under
`.working/imports/<run-id>/`, and any `opf init` scaffolding). It is NOT a round-trip editor of
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

Byte-canon (_byte_canon.py scanner, VER-CORE 3.1): the output is byte-canonical by construction. Every
string value and quoted key is rendered as an escaped basic string, so a body carrying a carriage
return, a trailing space, a control character, or a zero-width / bidirectional control codepoint is
represented by an escape sequence and never reaches the file bytes literally, where it would trip the
byte-canon gate. This is why a value that contains newlines is emitted as an escaped single-line basic
string rather than a literal `\"\"\"` multi-line string: a literal multi-line string cannot satisfy
byte-canon for an arbitrary captured body (a CRLF, a line with trailing whitespace, or a forbidden
codepoint would land in the bytes verbatim). The subset supports strings of any content, including
newlines; the canonical FORM of that support is escaping. _byte_canon is the authority for the
byte rules; the self-test reconciles this module's forbidden-codepoint set against it and runs every
emitted vector through _byte_canon.scan_bytes, so the two cannot drift. The en dash (U+2013) and em
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

The live leg is folded into `opf/tools/opf.py --self-test` (build plan section 3); this module is a library
consumed by U7 (import) and `opf init`, with no live/standalone mode beyond the self-test.
The self-tests require Linux fork/waitid, readable procfs and child-subreaper support.

Exit convention (matches the repo's gates): 0 clean, 1 a self-test finding, 2 misuse.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: _opf_emit.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import datetime
import math
import re
from pathlib import Path

import tomllib


class EmitError(Exception):
    """A value, key, or structure outside the constrained subset, or an output that fails to round-trip.
    Every path that raises it is fail-closed: no non-canonical or non-round-tripping bytes are returned."""


# A TOML bare key: one or more of A-Za-z0-9_-. Anything else (a dot, a space, an empty key, unicode) is
# rendered as a quoted basic-string key instead.
_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")

# Codepoints the byte-canon scanner forbids anywhere in a released text file (_byte_canon.py, VER-CORE 3.1):
# zero-width (U+200B..U+200D, U+2060 word joiner, U+FEFF) and the Unicode Bidi_Control set. They are
# escaped in emitted strings so they never appear literally. This constant is the authority's set; the
# self-test asserts it equals _byte_canon.FORBIDDEN, so a change there cannot silently pass here.
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


def _is_exception_spec(spec):
    """True only when `spec` is usable as an `except` operand: an exception CLASS (a subclass of
    BaseException), or a tuple of such classes (the empty tuple, which matches nothing, is valid). A
    non-exception value (an int, a string, a tuple carrying a non-exception) is rejected so the caller can
    substitute a match-nothing tuple rather than let `except <non-exception>` raise an uncontrolled
    TypeError at handling time (F8, guard-input-soundness)."""
    if isinstance(spec, type) and issubclass(spec, BaseException):
        return True
    if isinstance(spec, tuple):
        return all(isinstance(e, type) and issubclass(e, BaseException) for e in spec)
    return False


def emit_checked(document):
    """The staging contract: emit `document`, reparse the result, and confirm it is model-equivalent to
    the input before returning the text. Nothing that does not reparse or does not round-trip is ever
    returned, so a caller (U7 import staging, `opf init`) can stage the text knowing it is faithful.
    The two failure modes are defensive: a correct emitter never reaches them, so either is a fail-closed
    EmitError, never a silent degraded write."""
    text = emit(document)
    # Resolve the decode-error type BEFORE the try: `except tomllib.TOMLDecodeError` evaluates the
    # attribute at handling time, so a swapped tomllib lacking it would make the except clause itself
    # raise an uncontrolled AttributeError. Bind it defensively; a tomllib without the attribute yields
    # an empty tuple that matches nothing, so a reparse failure then falls to the value-free backstop
    # below rather than escaping.
    try:
        _decode_error = tomllib.TOMLDecodeError
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:  # noqa: BLE001 - a tomllib without TOMLDecodeError: match nothing, fail closed below
        _decode_error = ()
    # F8: the bound attribute is TRUSTED as an exception class only AFTER validating it. A tomllib-like
    # object whose TOMLDecodeError is a non-exception (the int 7, or a tuple carrying one) would make
    # `except _decode_error` raise an uncontrolled TypeError ("catching classes that do not inherit from
    # BaseException") when a reparse failure reaches the handler. Accept only an exception class or a tuple
    # of exception classes; anything else matches nothing (an empty tuple), so a reparse failure then falls
    # to the value-free BaseException backstop below rather than escaping (guard-input-soundness; fail closed).
    if not _is_exception_spec(_decode_error):
        _decode_error = ()
    # NARROW the decode-error spec to genuine Exception subclasses. _is_exception_spec accepts ANY
    # BaseException subclass (it must, to keep `except <spec>` from raising), but a substituted tomllib
    # whose TOMLDecodeError is a CANCELLATION class (KeyboardInterrupt/SystemExit/GeneratorExit, or any
    # other BaseException-that-is-not-Exception) would otherwise let `except _decode_error` convert genuine
    # control flow raised by loads() into an EmitError. Only Exception-subclass specs route to the
    # "did not reparse" conversion; a cancellation spec is narrowed to match-nothing (an empty tuple), and
    # the cancellation clause below is ORDERED FIRST as defence in depth (guard-input-soundness, fail closed).
    _cancel_classes = (KeyboardInterrupt, SystemExit, GeneratorExit)
    _decode_classes = _decode_error if isinstance(_decode_error, tuple) else (_decode_error,)
    if not all(isinstance(e, type) and issubclass(e, Exception) for e in _decode_classes):
        _decode_error = ()
    try:
        reparsed = tomllib.loads(text)
        if not _model_equal(document, reparsed):
            raise EmitError("emitted document did not round-trip to a model equal to its input; fail-closed")
    except _cancel_classes:  # genuine control flow re-raised FIRST, never converted (even by a hostile spec)
        raise
    except EmitError:
        raise
    except _decode_error:  # value-free so a hostile decode-error __str__ is never formatted into a diagnostic
        raise EmitError("emitted document did not reparse as TOML; fail-closed")
    except BaseException:  # noqa: BLE001 - fail-closed backstop mirroring emit(): a NON-TOMLDecodeError
        # reparse or comparison failure (a RecursionError from a >1000-part dotted key on 3.12/3.13, or a
        # MemoryError building the second tree or the comparison stack) becomes a value-free EmitError, so
        # emit_checked honours the same no-uncontrolled-exception contract as emit() and U7's `except
        # EmitError` fail-closed path is never bypassed by a leaked exception.
        raise EmitError("emitted document could not be reparsed or compared for the round-trip proof; "
                        "fail-closed")
    return text


# --- self-test --------------------------------------------------------------------------------------

def _load_byte_canon_authority():
    """Load the _byte_canon scanner from its pinned sibling FILE by explicit path, never via a bare
    `import` (which trusts sys.path) or the ambient sys.modules cache (which a poisoned entry could
    substitute with an always-clean scanner that would falsely certify the emitted bytes). module_from_spec
    + exec_module loads the real file without consulting or registering in sys.modules, so the authority
    is bound by file identity. sys.path is snapshotted and restored around the load (the authority
    inserts its own directory for its transitive imports). Any failure propagates so the caller fails
    closed; byte-canon cleanliness cannot be asserted without the genuine authority."""
    import importlib.util
    path = Path(__file__).resolve().parent / "_byte_canon.py"
    spec = importlib.util.spec_from_file_location("_opf_emit_byte_canon_authority", path)
    if spec is None or spec.loader is None:
        raise ImportError("no import spec for the byte-canon authority at {}".format(path))
    module = importlib.util.module_from_spec(spec)
    _saved_sys_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = _saved_sys_path
    return module


def _rejects(document):
    """True iff emit() rejects `document` with EmitError (the fail-closed subset boundary). Any other
    exception is a defect and is surfaced by the caller as a non-rejection."""
    try:
        emit(document)
        return False
    except EmitError:
        return True


class ChildStatusUnavailable(RuntimeError):
    """Cannot-evaluate: no observed wait status; never a successful fixture."""


class FixtureIncomplete(RuntimeError):
    """The requested fixture did not deliver its bound completion record."""


def _fixture_wait(pid, flags):
    """Keep ECHILD distinct; subprocess's wait/poll may synthesize a zero."""
    import os
    try:
        return os.waitpid(pid, flags)
    except OSError as exc:
        raise ChildStatusUnavailable("cannot collect fixture status: " + str(exc)) from exc


def _fixture_signal(pid, signum, pidfd=None, *, group=True, forked=False):
    """The sole numeric signal boundary. ECHILD never licenses a signal.

    Every caller passes ONLY the pid its own os.fork() just returned,
    with the pidfd the forking parent opened on it (_launch_fork's child,
    and the guardian's own subject in _fixture_drain): under
    D-385-PIDFD-HANDOFF, as read by the D-385-CURRENT-CHILD ruling, a
    process the caller did NOT fork is never signalled -- an adopted
    CURRENT child included. The retired drain signalled /proc-discovered
    adopted descendants here as proved current children; that license is
    gone: such a descendant is now reaped if it exits on its own, or
    NAMED, not signalled, in the drain refusal -- the disclosed
    orphan-escape residual. The waitid ownership check proves only
    CURRENT waitability, which an ADOPTED child also has (QA15 codex
    blocker: waitid(P_PID) selects the current child matching the
    number, not its historical identity), so it can never license a
    numeric send by itself. The numeric os.kill fallback (a pidfd-less
    host) therefore REQUIRES `forked=True`, the call site's same-call
    declaration that `pid` is this process's OWN direct, un-reaped fork
    -- fork-bound at the site, the _kill_proved_child review boundary,
    now enforced HERE instead of resting on call-site review alone:
    without it the numeric send is refused (False) and nothing is
    signalled. Never a numeric killpg (QA21 codex F3). `group` is
    retained for call-site
    symmetry and licenses nothing; concurrent foreign waiters remain
    outside this trusted test contract.
    """
    import os
    import signal

    def owned():
        try:
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return False
        return True

    if not owned():
        return False
    try:
        if pidfd is not None:
            signal.pidfd_send_signal(pidfd, signum)
        elif forked:
            os.kill(pid, signum)
        else:
            # D-385-CURRENT-CHILD: waitability without the caller's own-fork
            # declaration licenses NO numeric send (an adopted current child,
            # or an adopted reuse of a reaped number, would pass owned()).
            return False
    except ProcessLookupError:
        pass
    return True


def _fixture_pidfd(pid):
    import errno
    import os
    import signal
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return None
    try:
        return os.pidfd_open(pid)
    except OSError as exc:
        if exc.errno in (errno.ENOSYS, errno.EINVAL, errno.ESRCH):
            return None
        raise


def _fixture_subreaper():
    """Refuse before launching a subject without Linux subreaper support."""
    import ctypes
    import sys
    if sys.platform != "linux":
        raise ChildStatusUnavailable("fixture trees require Linux child subreaping")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    value = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(value), 0, 0, 0) != 0 or value.value != 1:
        raise ChildStatusUnavailable("cannot confirm child subreaping")


def _fixture_pdeathsig():
    """Arm PR_SET_PDEATHSIG(SIGKILL) in the subject: partial extra coverage for the
    documented wedged-guardian residual (a guardian killed while wedged can no longer
    drain its tree; the kernel then kills the subject when its parent dies). Descendants
    the subject forks do NOT inherit the flag, and where prctl is unavailable this fails
    closed to that documented residual: the drain/census stays the authoritative cleanup."""
    import ctypes
    import signal
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, int(signal.SIGKILL), 0, 0, 0)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def _fixture_check_parent(expected):
    """Immediately after arming pdeathsig, prove the parent is still the expected
    guardian: pdeathsig is not retroactive, so a subject first scheduled after its
    guardian already died would arm too late and run orphaned (QA18 codex F3). A
    reparented subject refuses HERE, before any user code, so an orphan can never
    run its callable; the refusal lands on fd 2 / exit 125 like any subject error."""
    import os
    if os.getppid() != expected:
        raise ChildStatusUnavailable(
            "subject orphaned before supervision: guardian {} is gone".format(expected))


def _fixture_close_all_except(keep):
    """First act of a new guardian: close EVERY inherited descriptor not in {0,1,2} | keep.
    The /proc/self/fd scan is the only authoritative census of the open-descriptor
    namespace (the layer already requires Linux; os.close_range is absent from this
    interpreter build). A failed census is a startup REFUSAL (recorded, exit 125),
    never a partial sweep: a soft-RLIMIT_NOFILE bound does not enumerate descriptors
    already open above it, so sweeping up to it would silently preserve them (QA18
    codex F4). There is no parent-side registry to publish to or go stale: an
    undeclared caller descriptor is closed here unconditionally, so a subject using
    one fails loudly (EBADF), never a sometimes-working leak, and a sibling call's
    endpoints (constructed or still under construction) never survive into an
    unrelated guardian."""
    import os
    kept = {0, 1, 2}
    kept.update(int(fd) for fd in keep)
    try:
        fds = sorted(int(name) for name in os.listdir("/proc/self/fd"))
    except (OSError, ValueError) as exc:
        raise ChildStatusUnavailable(
            "authoritative descriptor census unavailable: " + str(exc)) from exc
    for fd in fds:
        if fd not in kept:
            try:
                os.close(fd)
            except OSError:
                pass


def _fixture_enable_gc():
    """Last subject-side setup step: the guardian runs with gc disabled (the forked
    heap is the caller's, and a collection could close descriptor numbers the guardian
    legitimately reuses), but the SUBJECT runs arbitrary test code, and inheriting a
    disabled collector would leak reference cycles for the callable's whole life (QA18
    gemini F3). Re-enable it just before the callable runs."""
    import gc
    gc.enable()


def _fixture_send_subject(peer, subject, subject_fd):
    """Deliver the subject receipt on the control socket: no subject RUNS USER CODE
    without a delivered receipt of it (the acknowledgment that releases the subject is
    written only after this send returns). The one-byte-plus-cmsg send on an empty
    socketpair cannot block, and the receipt stays buffered for the caller even if the
    guardian is later SIGKILLed. The guardian treats a send failure as fatal (recorded,
    subject drained, exit 125). Without a pidfd only the pid is sent and escalation
    stays guardian-only: a bare pid cannot exclude the reaped-before-freeze recycling
    window."""
    import socket
    payload = str(subject).encode("ascii")
    if subject_fd is not None:
        socket.send_fds(peer, [payload], [subject_fd])
    else:
        peer.send(payload)


def _fixture_ack_subject(fd):
    """Release the subject, strictly AFTER the receipt send returned: an acknowledged
    subject is always addressable through its delivered receipt (QA18 codex F2). A
    subject that already died is tolerated (its wait status is authoritative), and the
    write end closes either way, so a guardian that dies before acknowledging always
    hands the blocked subject an EOF."""
    import os
    try:
        os.write(fd, b"A")
    except OSError:
        pass  # the subject is already gone: supervision reads its status
    finally:
        os.close(fd)


def _fixture_await_ack(fd):
    """Block the subject until the guardian acknowledges ownership. Until this byte
    arrives the subject runs no user code and cannot create descendants, so a guardian
    wedged before the receipt send strands at most one blocked, pdeathsig-covered
    process with no tree (QA18 codex F2). EOF -- every write end died with the
    guardian -- is a refusal: an unowned subject never runs its callable."""
    import os
    try:
        if os.read(fd, 1) != b"A":
            raise ChildStatusUnavailable(
                "guardian gone before ownership acknowledgment: subject refuses to run")
    finally:
        os.close(fd)


# Sentinel for a /proc entry that EXISTS but cannot be read: never proof of
# exit, never silently equated with an exited process (fix 2z, codex BLOCKER 2
# / gemini F2). Callers account such entries against any "tree" claim.
_FIXTURE_UNREADABLE = object()


def _fixture_stat_fields(target):
    """Read the post-comm fields of /proc/<target>/stat, through os.open and
    os.read with the descriptor close routed as a _cleanup_boundary step
    (fix 8, QA29 codex BLOCKER 1 / claude MINOR 1: Path.read_bytes hid a
    standard-library context manager whose INTERNAL close ran outside every
    boundary, so a read-born cancellation could be displaced before this
    function's handler saw it; module-owned cleanup only ever runs through
    the boundary, maintainer ruling PD-335). Returns the fields; None ONLY
    for an ABSENT entry (FileNotFoundError/ProcessLookupError: the process
    exited or raced away mid-read -- the only OSErrors that read as exited,
    fix 2z codex BLOCKER 2 / gemini F2); or _FIXTURE_UNREADABLE for an entry
    that exists but cannot be read (e.g. EACCES/EIO), which is never proof
    of exit. TimeoutError/InterruptedError are OSError subclasses carrying
    deadline/cancellation semantics and PROPAGATE (fix 2y, codex F3);
    non-I/O failures (a parse defect) propagate too."""
    import os
    fd = None
    pending = None
    stat = b""

    def close_stat_fd():
        if fd is not None:
            os.close(fd)

    try:
        try:
            fd = os.open("/proc/" + str(target) + "/stat",
                         os.O_RDONLY | os.O_CLOEXEC)
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                stat += chunk
        except BaseException as exc:
            # Capture the exception already propagating into the close
            # below (fix 8, QA29 codex BLOCKER 1): a read-born
            # cancellation must stay the outward exception even when the
            # descriptor close itself fails.
            pending = exc
            raise
        finally:
            _cleanup_boundary(pending, close_stat_fd,
                              "stat descriptor close")
    except OSError as exc:
        if isinstance(exc, (TimeoutError, InterruptedError)):
            raise
        if isinstance(exc, (FileNotFoundError, ProcessLookupError)):
            return None
        return _FIXTURE_UNREADABLE
    return stat.rsplit(b")", 1)[1].split()


def _fixture_group_pinned(group, guardian_pid):
    """License addressing a dead leader's group MEMBERS (each through its own
    verified pidfd, never a numeric kill): True only when a CURRENT member of the
    group is parented by the caller's own guardian while that guardian provably
    cannot reap (confirmed stopped, or an unreaped zombie). The guardian is the
    tree's subreaper, so every orphaned same-group descendant is its child, and
    a member found under a stopped-or-zombie parent stays unreaped -- live or
    zombie, it holds the pgid -- for the rest of the kill sequence. A guardian
    that is running (its stop unconfirmed), gone from /proc, UNREADABLE in
    /proc (its state unconfirmable, fix 2z), or childless in the group
    licenses nothing: the group may have emptied and its pgid been recycled
    by an unrelated process group (QA20 claude F1). An unreadable candidate
    entry can never positively match, so it never licenses either; refusing
    the pin only downgrades toward the disclosed subject-only kill, never
    toward a claimed tree."""
    import os
    import time

    deadline = time.monotonic() + _FIXTURE_CLEANUP_GRACE
    while True:
        fields = _fixture_stat_fields(guardian_pid)
        if fields is None or fields is _FIXTURE_UNREADABLE:
            return False
        if fields[0] in (b"T", b"Z"):
            break
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.005)
    try:
        entries = os.listdir("/proc")
    except OSError as exc:
        # Narrowed (fix 2y, codex F3): a cancelled census must refuse by
        # propagation, never read as "not pinned".
        if isinstance(exc, (TimeoutError, InterruptedError)):
            raise
        return False
    for name in entries:
        if not name.isdecimal():
            continue
        fields = _fixture_stat_fields(name)
        if (fields is not None and fields is not _FIXTURE_UNREADABLE
                and int(fields[1]) == guardian_pid
                and int(fields[2]) == group):
            return True
    return False


def _fixture_kill_group_members(group, signum, handoffs, leader=None):
    """Signal every live CURRENT member of process group `group` whose pidfd
    its FORKING PARENT handed off (D-385-PIDFD-HANDOFF): `handoffs` maps a
    member pid to the pidfd that member's forking parent opened immediately
    after fork -- while the un-reaped child pinned the number -- and passed
    over SCM_RIGHTS. That descriptor is the ONLY delivery license. /proc is
    read here to OBSERVE which members are live, NEVER to license a signal:
    the retired ancestry route (the `anchored` parent-chain walk) opened a
    pidfd LOCALLY on each /proc-discovered number and licensed its kill by a
    chain of /proc reads reaching a caller anchor; D-385 retires that whole
    proof class, so NO pidfd is opened here at all, and a live member with
    no handoff is SKIPPED -- reported, never signalled -- the fail-closed
    outcome. At runtime a subject's own forks arrive with no handoff, so the
    census is report-only there and those descendants join the disclosed
    orphan-escape residual. Handoff descriptors stay OWNED BY THE CALLER:
    none is closed here. NEVER a numeric os.killpg (QA21 codex F3: once a
    group's leader is reaped the numeric pgid is free for reuse, so a
    numeric group kill can address an unrelated group). `leader` -- the
    group leader the caller addresses through its own ALREADY-HELD pidfd --
    is excluded here and signalled by the caller strictly AFTER the members,
    so no member is stranded by its leader dying mid-census (fix 2y, codex
    F2). Exited (zombie) members hold their pgid but take no signal and are
    no longer addressable members; a /proc entry that EXISTS but cannot be
    READ is never proof of exit: it is accounted too, so no caller can claim
    a killed tree past it (fix 2z, codex BLOCKER 2 / gemini F2) -- but it is
    accounted SEPARATELY, as a POSSIBLE member, because its group membership
    was never established (round 24, claude F1), and it is accounted BEFORE
    any handoff lookup: an unverifiable entry is never signalled, handoff or
    not. TimeoutError/InterruptedError (deadline/cancellation semantics) and
    non-I/O failures always PROPAGATE (fix 2y, codex F3); with no descriptor
    opened here there is no cleanup step that could displace them. Returns
    (delivered, skipped, unverifiable): the pids signalled through their
    handed-off descriptors; every pid OBSERVED as a live group member but
    not signalled (no forking-parent handoff, a descriptor-less handoff
    entry, or a non-exit send failure), with skipped None when the /proc
    census itself was unreadable (membership unknown); and every /proc entry
    that exists but could not be read -- possible members, never established
    ones. Callers account all three and never claim the tree was killed past
    this census. Hosts without pidfd have no handoffs and address no members
    (the degraded-escalation disclosures cover them)."""
    import os
    import signal
    targets = {int(pid): handoffs[pid] for pid in handoffs}
    delivered, skipped, unverifiable = [], [], []
    try:
        entries = os.listdir("/proc")
    except OSError as exc:
        if isinstance(exc, (TimeoutError, InterruptedError)):
            raise
        return delivered, None, unverifiable
    for name in entries:
        if not name.isdecimal():
            continue
        member = int(name)
        if leader is not None and member == int(leader):
            continue  # the caller signals the leader LAST, via its held pidfd
        fields = _fixture_stat_fields(member)
        if fields is _FIXTURE_UNREADABLE:
            # The entry EXISTS but cannot be read: it may be a live member,
            # and an unreadable record is never proof of exit. Account it as
            # a POSSIBLE member -- its membership was never established
            # (round 24, claude F1) -- so no caller may claim the tree past
            # this census (fix 2z, codex BLOCKER 2 / gemini F2). The handoff
            # map is never consulted for it: unverifiable, not delivered.
            unverifiable.append(member)
            continue
        if fields is None or int(fields[2]) != group or fields[0] == b"Z":
            continue
        if member not in targets or targets[member] is None:
            # FAIL CLOSED (D-385-PIDFD-HANDOFF): a live member whose forking
            # parent supplied no descriptor is reported, never signalled --
            # no local pidfd open, no ancestry walk, no numeric kill.
            skipped.append(member)
            continue
        try:
            signal.pidfd_send_signal(targets[member], signum)
        except (ProcessLookupError, OSError) as exc:
            if isinstance(exc, (TimeoutError, InterruptedError)):
                raise
            if not isinstance(exc, ProcessLookupError):
                skipped.append(member)  # handed-off member, delivery failed
            continue
        delivered.append(member)
    return delivered, skipped, unverifiable


def _fixture_verify_group_kill(group):
    """OBSERVATION, never delivery, licenses the "tree" claim (fix 2z,
    premise change; narrowed by the maintainer ruling
    PD-335-TREE-CLAIM-STALL): after the member kills and the leader kill,
    while the frozen guardian still pins the group, poll /proc within the
    cleanup grace until TWO CONSECUTIVE censuses each see NO live,
    signalable member of `group` and no unreadable candidate entry -- only
    that observation returns ("tree", []), and it means exactly that
    observation, never a proof that the whole tree died: a member can fork
    and exit between reads, so a continuously forking chain, or pid
    wraparound placing a child below the readdir cursor, can evade even two
    back-to-back clean censuses (a single clean census is weaker still: a
    survivor missing from one snapshot can appear in the next). Callers
    word every "tree" disclosure as an observation with this residual,
    never as proof. Zombies are dead, not signalable: an unreaped member
    holds the pgid but cannot run or fork. A member still live when the
    bound expires (e.g. one forked between the kill census's snapshot and
    its kills, fix 2z claude F1) is NAMED in
    ("partial", (members, unverifiable)); an entry that exists but cannot
    be read might be such a member, so it prevents the claim too, named as
    unverifiable -- a possible member, never an established one (round 24,
    claude F1); an unreadable /proc census is ("partial", None): membership
    unknown. Reads only: no pidfd is opened and no signal is sent here, so
    the no-census contract of the guardian-dead paths is untouched.
    TimeoutError/InterruptedError propagate (fix 2y, codex F3)."""
    import os
    import time
    deadline = time.monotonic() + _FIXTURE_CLEANUP_GRACE
    clean = 0
    while True:
        survivors, unverifiable = [], []
        try:
            entries = os.listdir("/proc")
        except OSError as exc:
            if isinstance(exc, (TimeoutError, InterruptedError)):
                raise
            return ("partial", None)
        for name in entries:
            if not name.isdecimal():
                continue
            fields = _fixture_stat_fields(name)
            if fields is _FIXTURE_UNREADABLE:
                unverifiable.append(int(name))
                continue
            if fields is None or fields[0] == b"Z":
                continue
            if int(fields[2]) == group:
                survivors.append(int(name))
        if not survivors and not unverifiable:
            clean += 1
            if clean >= 2:
                return ("tree", [])
            continue  # the second census runs back to back, never accepted alone
        clean = 0
        if time.monotonic() >= deadline:
            return ("partial",
                    (sorted(set(survivors)), sorted(set(unverifiable))))
        time.sleep(0.005)


# Fix 6 (QA27 codex BLOCKER 1/2; premise change): pending-cancellation
# priority is ONE shared boundary, never a per-site idiom. Fix 7 (QA28
# codex BLOCKER 1/2, claude MINOR 1, gemini MAJOR 1) made the scope
# COMPUTED. Fix 8 (QA29 codex BLOCKER 1/2, claude MAJOR 1 / MINOR 1/2,
# gemini BLOCKER / MAJOR; maintainer ruling PD-335, narrow and
# disclose) scopes the guarantee to what this module OWNS and widens it
# to the whole close lifecycle: every MODULE-OWNED cleanup reachable
# from the lifecycle entry points -- _FixtureProcess.close,
# _finish_close, _interrupt_collect, _escalate and
# _address_failed_subject -- runs through _cleanup_boundary below, and
# an AST structural leg in `opf.py --self-test` (census-exception)
# COMPUTES that closure from the module source each run and FAILS
# CLOSED on what it cannot resolve: a call edge that is not in-module
# code, a builtin, a declared stdlib module, or one of the leg's
# DISCLOSED external-object primitives is a cannot-evaluate FAILURE,
# never a silent pass -- aliased calls, attribute calls and function
# references passed as arguments, boundary steps included, are resolved
# and traversed or turn the leg red (fix 8, QA29 codex BLOCKER 2 /
# claude MAJOR 1). DISCLOSED RESIDUAL (PD-335): the guarantee covers
# cleanup this module OWNS; cleanup that runs INSIDE the standard
# library or other external code -- a stdlib helper's internal context
# manager (the Path.read_bytes class, QA29 codex BLOCKER 1), socket and
# file close internals, threading lock release -- is disclosed, never
# an enforced property, which is why the module's own /proc reads go
# through os.open/os.read with the descriptor close as a boundary step.
# A cancellation raised BY a cleanup step becomes the pending
# cancellation for every later step in that cleanup sequence (QA28
# codex BLOCKER 1). KeyboardInterrupt joins TimeoutError and
# InterruptedError in the pending set, and the tiers are harmonized
# (QA28 claude MINOR 3; QA29 gemini MAJOR: the direct guardian
# backstop's cancellation tier is one explicit _PENDING_CANCELLATIONS
# guard): at the boundary AND at the recording/retry tier, a cleanup a
# cancellation interrupted records nothing, whichever of the three
# cancellation types it was. Fix 9 (QA30 codex BLOCKER 1/2, claude
# MINOR 1/2/3) makes the CAPTURE shape sound and the disclosure exact:
# a handler that captures its exception for a deferred re-raise routes
# every later cleanup through the boundary with the captured object
# pending, and every later path re-raises THAT captured object -- the
# structural leg walks the code that follows the capture and fails
# closed on any unprotected call, on a raise of any other name, and on
# a path that could complete without the re-raise; a disclosed method
# NAME counts only for a receiver PROVEN external (a builtin-typed
# value or an object built by a call into an imported module), never
# for one that could be module-owned; and every module /proc read,
# the guardian-side census included, goes through os.open/os.read with
# the descriptor close as a boundary step.
_PENDING_CANCELLATIONS = (TimeoutError, InterruptedError, KeyboardInterrupt)


def _chain_ids(exc):
    """ids of every exception reachable from exc over __cause__ and
    __context__ edges, exc included, cycle-guarded."""
    seen, frontier = set(), [exc]
    while frontier:
        node = frontier.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        frontier.append(node.__cause__)
        frontier.append(node.__context__)
    return seen


def _attach_beneath(pending, suppressed, site):
    """Keep `suppressed` reachable beneath `pending` WITHOUT rewriting
    pending's own pre-existing chain (QA26 claude MINOR 1): first drop
    suppressed's implicit back-edge to pending (set when it was raised
    inside the caller's finally), so the outward chain stays acyclic;
    then attach the suppressed EXCEPTION OBJECT as pending.__cause__ when
    that slot is free, else append it at the tail of pending's
    cause/context chain (QA27 codex: the object and its own chain stay
    reachable, never just a repr). REACHABILITY, never display, is the
    contract (QA28 claude MINOR 2): the tail walk follows __cause__-then-
    __context__ edges and ignores __suppress_context__, so beneath a
    `raise ... from None` edge the attached object is reachable
    programmatically but not rendered in the traceback. Only when no
    attachment slot can be taken safely -- the two chains already share a
    node (sharing is not itself a cycle, but attaching under a shared
    structure could create one, so the refusal is deliberately
    CONSERVATIVE), or a cycle occupies the tail slots -- fall back to a
    note naming the site and the suppressed failure, computed inside the
    same protection. Every diagnostic here, repr included, runs inside
    the caller's protection (_cleanup_boundary), so a raising __repr__ or
    hostile chain attribute can never displace the cancellation (QA27
    codex BLOCKER 2)."""
    tail, seen = suppressed, set()
    while tail is not None and id(tail) not in seen:
        seen.add(id(tail))
        if tail.__context__ is pending:
            tail.__context__ = None
            break
        tail = tail.__context__
    pending_chain = _chain_ids(pending)
    if id(suppressed) in pending_chain:
        return  # already reachable beneath the pending cancellation
    if not (pending_chain & _chain_ids(suppressed)):
        if pending.__cause__ is None:
            keep = pending.__suppress_context__
            pending.__cause__ = suppressed
            pending.__suppress_context__ = keep
            return
        tail, seen = pending, set()
        while id(tail) not in seen:
            seen.add(id(tail))
            below = (tail.__cause__ if tail.__cause__ is not None
                     else tail.__context__)
            if below is None:
                break
            tail = below
        if tail.__cause__ is None and tail.__context__ is None:
            tail.__context__ = suppressed
            return
    try:
        detail = repr(suppressed)
    except BaseException:
        detail = "<unrepresentable " + type(suppressed).__name__ + ">"
    pending.add_note(site + " failure kept beneath this pending "
                     "cancellation: " + detail)


def _cleanup_boundary(pending, step, site):
    """The ONE exception boundary every escalation-path cleanup step runs
    through (fix 6: QA25, QA26 and QA27 each closed one more hand-coded
    site; fix 7: the census-exception structural leg computes the
    escalation call-graph closure from the module source each run, so
    the routed scope is mechanical, never hand-named). `pending` is the exception
    already propagating into the caller's finally (None when the caller
    completed normally); `step` is the cleanup callable; `site` names the
    step for diagnostics. Contract, exactly: a step that returns changes
    nothing; a step failure with NO pending cancellation
    (_PENDING_CANCELLATIONS) propagates unchanged, behaviour-identical to
    the hand-coded sites this replaces; a step failure WITH a pending
    cancellation never displaces it -- the cancellation is re-raised as
    the outward exception, its pre-existing __cause__/__context__ chain
    intact, the step's failure kept reachable beneath it by
    _attach_beneath (cause slot, else chain tail, else, only when no
    acyclic attachment exists, a note), and the whole attachment, every
    walk and repr included, runs inside this boundary's own protection,
    so a hostile exception degrades the attachment, never the outward
    exception (QA27 codex BLOCKER 2). The re-raise happens OUTSIDE the
    handler that caught the step's failure, so CPython leaves the
    cancellation's __context__ untouched (QA26 claude MINOR 1).
    Attachment is BEST-EFFORT, and that means exactly this (QA28 gemini):
    a failure raised inside the attachment machinery itself -- a hostile
    chain attribute, a MemoryError under pressure, even an asynchronous
    KeyboardInterrupt landing there -- is caught and DISCARDED, the
    attachment may then be partial or absent, and a cancellation born
    inside the attachment is deliberately LOST; the ALREADY-PENDING
    cancellation is still re-raised, because letting an attachment fault
    displace it is the exact failure class this boundary exists to
    close."""
    try:
        return step()
    except BaseException as cleanup_exc:
        if not isinstance(pending, _PENDING_CANCELLATIONS):
            raise
        suppressed = cleanup_exc
    try:
        _attach_beneath(pending, suppressed, site)
    except BaseException:
        # Attachment is best-effort by contract (QA28 gemini): ANY
        # failure inside the attachment machinery -- MemoryError, a
        # hostile chain attribute, an asynchronous KeyboardInterrupt
        # landing here -- is discarded and never displaces the pending
        # cancellation; a cancellation born inside the attachment is
        # deliberately lost.
        pass
    raise pending


def _fixture_escalate_subject(subject, subject_fd, *, guardian_pid=None,
                              member_handoffs=None):
    """Kill a receipt-identified subject, addressing OWNERSHIP-VERIFIED
    targets only, each through its own pidfd -- NEVER a numeric group kill,
    and NEVER a member census anchored on the subject's bare NUMERIC pid
    (fix 2y, codex F1: only unreapability pins a pid or pgid number against
    reuse). Freeze first: SIGSTOP through the subject's ALREADY-HELD pidfd
    is identity-safe and stops a live leader before any kill, so it cannot
    fork or reap mid-sequence; the freeze runs INSIDE the exception-safe
    kill protection (round 24, codex boundary), so a raising freeze -- a
    re-raised cancellation or a non-OSError delivery fault -- never skips
    the held-pidfd SIGKILL. Guardian ownership licenses only RUNNING the
    member census: `guardian_pid` -- the caller's own unreaped child,
    certified frozen -- is the tree's subreaper, and _fixture_group_pinned
    confirms it stopped-or-zombie and still parenting a group member, which
    pins the pgid NUMBER against reuse while the census reads /proc. Member
    DELIVERY is licensed per member and ONLY by `member_handoffs` -- a map
    of member pid to the pidfd that member's FORKING PARENT opened and
    handed off over SCM_RIGHTS (D-385-PIDFD-HANDOFF; the retired ancestry
    walk licensed delivery by /proc parent chains and is gone) -- so a live
    member with no handoff is SKIPPED and NAMED, never signalled: with the
    runtime's empty map the census is report-only and the subject's own
    forks join the disclosed orphan-escape residual. Handed-off members die
    BEFORE the leader (the census excludes the leader; its held-pidfd
    SIGKILL runs last, fix 2y codex F2), and the subject SIGKILL is EXCEPTION-SAFE (fix
    2z, codex BLOCKER 3): a raising census never strands the frozen leader,
    and the census exception still propagates after the kill -- through the
    shared _cleanup_boundary (fix 6, QA27 codex BLOCKER 1), so a
    cancellation already propagating (a TimeoutError raised at the freeze)
    stays the outward exception even when that SIGKILL itself fails, the
    kill failure -- any failing send but ProcessLookupError, which proves
    the leader already exited and is no failure to keep -- re-raised into
    the boundary and kept reachable beneath it (fix 14, QA35 codex MAJOR:
    the EPERM-class faults the send can raise were recorded in the
    survivor flag only and dropped from that chain); the member census
    opens and closes NO descriptors of its own -- handoff descriptors stay
    caller-owned (D-385-PIDFD-HANDOFF) -- so a member-send cancellation
    propagates outward directly (fix 7's per-pidfd close boundary was
    retired with the census's local opens). The "tree"
    outcome rests on OBSERVATION, never on the kill sends alone (fix 2z,
    premise change; maintainer ruling PD-335-TREE-CLAIM-STALL): while the
    guardian stays frozen, a bounded verification census
    (_fixture_verify_group_kill) must observe NO live, signalable group
    member in TWO CONSECUTIVE clean censuses -- and even that outcome is an
    observation, never a proof (a continuously forking chain or pid
    wraparound can evade it; every disclosure says so). A member still live
    at the bound (e.g. forked past the kill snapshot), an unreadable entry
    (a possible member, accounted apart from established members, round 24
    claude F1), or a leader SIGKILL failing with anything but
    ProcessLookupError (fix 2z, gemini F1) downgrades the outcome to
    "partial", naming what remains where it is known, never "tree". With NO
    guardian ownership (guardian_pid absent -- the guardian
    is dead -- or the census unpinned) NO census runs at all: no new pidfds
    are opened and ONLY the subject is signalled through its already-held
    pidfd; its descendants are the documented orphan-escape residual (D2),
    which the caller DISCLOSES as unaddressed (pdeathsig and the ack-EOF
    gate are the partial coverage) and never reports as a killed tree.
    Returns ("tree", []) ONLY when the guardian-anchored census addressed
    every OBSERVED member (a census can only address members it observed)
    AND the verification census observed none left alive in two
    consecutive clean passes (an observation, never a proof),
    ("partial", (member-pids, unverifiable-pids) or None) when members were
    skipped, observed surviving or unkillable, entries were unverifiable
    (possible members), or the census or its verification was unreadable
    (None: membership unknown), or ("subject-only", None)."""
    import signal
    outcome = ("subject-only", None)
    leader_kill_failed = False
    pending = None

    def kill_leader():
        # The held-pidfd subject SIGKILL runs even if the freeze or the
        # census raised (fix 2z, codex BLOCKER 3); the earlier exception
        # still propagates, after the kill, through _cleanup_boundary.
        nonlocal leader_kill_failed
        try:
            signal.pidfd_send_signal(subject_fd, signal.SIGKILL)
        except (ProcessLookupError, OSError) as exc:
            if isinstance(exc, (TimeoutError, InterruptedError)):
                raise
            if not isinstance(exc, ProcessLookupError):
                # A leader this escalation could NOT kill is a
                # survivor; ProcessLookupError alone proves the leader
                # already exited. THIS line only sets the survivor
                # flag. What the flag does happens BELOW, after the
                # kill finally, where the enclosing
                # _fixture_escalate_subject computes its return
                # value: a would-be "tree" claim becomes a
                # partial naming the subject, a payload-carrying
                # "partial" adds the subject, and the no-guardian
                # ("subject-only", None) and ("partial", None)
                # outcomes stay unnamed (fix 2z, gemini F1; scoped
                # fix 16, QA37 codex MINOR; reworded fix 17, QA38
                # gemini; enclosing function named fix 18, QA39
                # gemini).
                leader_kill_failed = True
                if isinstance(pending, _PENDING_CANCELLATIONS):
                    # THIS branch runs only with a cancellation
                    # already pending (fix 14, QA35 codex MAJOR): the
                    # survivor flag alone would drop this kill failure
                    # from the cancellation's chain -- the docstring
                    # promises it stays reachable beneath it -- so
                    # re-raise the failure into the boundary, which
                    # attaches it beneath the pending cancellation and
                    # re-raises the cancellation itself. With nothing
                    # pending, or an ordinary failure pending, this
                    # branch is NOT taken: kill_leader returns with
                    # only the flag set, the flag stays the whole
                    # record of this kill failure, and naming the
                    # survivor is the outcome handling BELOW -- which
                    # an ordinary pending failure that re-raises past
                    # it never reaches, leaving NO outcome (fix 15,
                    # QA36 codex/claude MINOR; reworded fix 17, QA38
                    # gemini).
                    raise

    try:
        # The freeze runs INSIDE the kill protection (round 24, codex
        # boundary): an exception here -- a re-raised cancellation, or a
        # non-OSError delivery fault -- must still reach the finally's
        # held-pidfd SIGKILL, never leave the subject frozen and unkilled.
        try:
            signal.pidfd_send_signal(subject_fd, signal.SIGSTOP)
        except (ProcessLookupError, OSError) as exc:
            # Narrowed (QA21 gemini F2): TimeoutError/InterruptedError are
            # OSError subclasses carrying deadline/cancellation semantics,
            # never swallowed.
            if isinstance(exc, (TimeoutError, InterruptedError)):
                raise
        if guardian_pid is not None and _fixture_group_pinned(subject, guardian_pid):
            delivered, skipped, unverifiable = _fixture_kill_group_members(
                subject, signal.SIGKILL,
                member_handoffs if member_handoffs is not None else {},
                leader=subject)
            if skipped is None:
                outcome = ("partial", None)
            elif skipped or unverifiable:
                outcome = ("partial", (sorted(skipped), sorted(unverifiable)))
            else:
                outcome = ("tree", [])
    except BaseException as exc:
        # Capture the exception already propagating into the kill finally:
        # a pending cancellation must stay the outward exception even when
        # the held-pidfd SIGKILL itself fails (fix 6, QA27 codex BLOCKER 1).
        pending = exc
        raise
    finally:
        _cleanup_boundary(pending, kill_leader, "held-pidfd subject SIGKILL")
    if outcome[0] == "tree":
        # The kill sends alone never license the claim (fix 2z): observe the
        # group to quiescence while the guardian stays frozen.
        outcome = (("partial", ([int(subject)], [])) if leader_kill_failed
                   else _fixture_verify_group_kill(subject))
    elif (leader_kill_failed and outcome[0] == "partial"
            and outcome[1] is not None):
        members, unverifiable = outcome[1]
        outcome = ("partial",
                   (sorted({*members, int(subject)}), unverifiable))
    return outcome


def _fixture_children():
    """Census by PPID; task/children can transiently omit adopted children.
    OBSERVATION only (D-385-CURRENT-CHILD): the drain names these pids in
    its refusal and reaps the exited ones; no census pid is ever a signal
    target.
    The per-entry stat read goes through os.open/os.read with the descriptor
    close as a _cleanup_boundary step (fix 9, QA30 claude MINOR 2: this
    guardian-side census still read /proc via Path.read_bytes, so the fix-8
    header claim that the module's own /proc reads never hide a stdlib
    context manager was not literally true of the module; it now is).
    Exception behaviour is unchanged: an entry that disappears mid-read
    (FileNotFoundError/ProcessLookupError) is skipped, every other failure
    propagates so the caller refuses."""
    import os
    owner = os.getpid()
    pids = []
    fd = None
    pending = None

    def close_stat_fd():
        if fd is not None:
            os.close(fd)

    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            stat = b""
            fd = None
            pending = None
            try:
                try:
                    fd = os.open(entry.path + "/stat",
                                 os.O_RDONLY | os.O_CLOEXEC)
                    while True:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            break
                        stat += chunk
                except BaseException as exc:
                    # Capture the exception already propagating into the
                    # close below: a read-born cancellation must stay the
                    # outward exception even when the descriptor close
                    # itself fails (fix 9, the _fixture_stat_fields shape).
                    pending = exc
                    raise
                finally:
                    _cleanup_boundary(pending, close_stat_fd,
                                      "stat descriptor close")
            except (FileNotFoundError, ProcessLookupError):
                continue  # an unrelated process disappeared during enumeration
            # comm is parenthesized and may contain spaces and ')' characters.
            fields = stat.rsplit(b")", 1)[1].split()
            if int(fields[1]) == owner:
                pids.append(int(entry.name))
    return pids


# Cleanup has its own minimum grace after execution expires. While execution
# time remains, use that remaining budget instead if it is larger. This is a
# polling bound, not a guarantee that blocking kernel operations will return.
_FIXTURE_CLEANUP_GRACE = 5.0


def _fixture_cleanup_deadline(execution_deadline=None):
    import time
    floor = time.monotonic() + _FIXTURE_CLEANUP_GRACE
    return floor if execution_deadline is None else max(floor, execution_deadline)


def _fixture_drain(subject, subject_fd=None, *, deadline=None, subject_ref=None):
    """Dedicated single-threaded subreaper: every child belongs to this fixture.

    Kill ONLY the owned subject -- this guardian's own direct fork, through
    the descriptor the fork opened -- then COLLECT: adopted descendants that
    reparent here (nested guardians and descendants in other sessions
    included) are reaped as they exit, NEVER signalled (D-385-PIDFD-HANDOFF
    as read by the D-385-CURRENT-CHILD ruling: a process this guardian did
    not fork is never a signal target, an adopted CURRENT child included;
    the retired drain SIGKILLed every /proc-census pid here as a proved
    current child). Only kernel ECHILD proves completion. /proc read
    failures refuse; an empty snapshot is retried, never treated as
    completion or an immediate contradiction. The caller supplies one
    cleanup deadline, shared by normal and failure paths; absent one, the
    named minimum grace applies. Expiry yields cannot-evaluate, never
    success: the refusal NAMES every still-live adopted descendant, by pid,
    as NOT signalled -- the disclosed orphan-escape residual, left running
    (such a descendant may therefore OUTLIVE the refusing case and its
    fixture process, bounded only by the fixture's own self-limit -- a
    bounded sleep or gate deadline -- until its release gate or EOF ends
    it). When the PPID census transiently names nothing while kernel
    waitid still answers for a child, the refusal says exactly that -- a
    waitable child remains whose identity the census missed -- never an
    empty name list presented as the full survivor set (QA15 claude M2);
    while the subject is still bound and unreaped, the refusal names the
    SUBJECT as that waitable child and any further unnamed child as
    possible but unproven (QA16 claude m2).
    `subject_ref`, a single-element list owned by the caller, BINDS the
    subject's identity across retries (QA15 codex blocker): the drain
    reads the subject number from it and CLEARS it the moment the subject
    is reaped, so a second drain call with the same holder -- the
    guardian's failure-path retry -- can never signal that number again
    after the reap freed it for reuse by an adopted process. The reap and
    the clear run with SIGINT and SIGTERM blocked on the calling thread
    (_fixture_mask_cancellation, prior mask restored exactly in a finally;
    QA16 claude m1), so a cancellation landing between them stays
    kernel-pending and is raised only at the restore, after the holder is
    cleared; outside that guarantee, as for close()/poll(), are a signal
    whose interpreter-level flag tripped before the block landed, other
    signals with raising handlers, and non-signal asynchronous exceptions.
    Absent a holder, a fresh one is bound to this one call. The entry kill carries
    the drain contract's own-fork declaration (forked=True): `subject` is
    the calling guardian's own direct fork, and once the holder is
    cleared no numeric retry can reach its number. Syscalls still require
    kernel progress. Subjects attacking their guardian are outside this
    trusted harness's contract.
    """
    import os
    import signal
    import time
    status = None
    if deadline is None:
        deadline = _fixture_cleanup_deadline()
    if subject_ref is None:
        subject_ref = [subject]
    subject = subject_ref[0]
    # Cancel the owned subject immediately, even while a census is empty --
    # but ONLY while the holder still binds it: once the subject was reaped
    # (by this call or an earlier one sharing the holder), its number may
    # already identify an ADOPTED process, and no retry may signal it
    # (QA15 codex blocker; D-385-CURRENT-CHILD).
    if subject is not None:
        _fixture_signal(subject, signal.SIGKILL, subject_fd, forked=True)
    while True:
        try:
            os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return status
        # Reap every child already dead; an adopted live one is never signalled.
        while True:
            # The reap and the holder clear are ONE step with respect to the
            # caller's cancellation signals (QA16 claude m1): a SIGINT/SIGTERM
            # landing between them stays pending until the restore below, so
            # the holder is never left bound to an already-reaped number.
            prior = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            try:
                _fixture_mask_cancellation()
                try:
                    waited, raw = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    return status
                if waited == 0:
                    break
                if subject is not None and waited == subject:
                    status = raw
                    subject = subject_ref[0] = None  # the number is now reusable
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, prior)
        if time.monotonic() >= deadline:
            residual = sorted(pid for pid in _fixture_children()
                              if subject is None or pid != subject)
            if residual:
                raise ChildStatusUnavailable(
                    "descendant cleanup deadline: ECHILD not observed; adopted "
                    "descendants {} NOT signalled (D-385-CURRENT-CHILD: no "
                    "forking-parent handoff reaches this guardian), left as "
                    "the disclosed orphan-escape residual".format(residual))
            # QA15 claude M2: the deadline is reached only after waitid
            # answered for a child, so an empty census names the census's
            # own transient miss, never a completed drain or a full list.
            # QA16 claude m2: the census excludes the subject, so while the
            # subject is still bound and unreaped it alone explains the
            # answer; a further unnamed child is then possible, not proven.
            if subject is not None:
                raise ChildStatusUnavailable(
                    "descendant cleanup deadline: ECHILD not observed and the "
                    "/proc census named no adopted descendant: the subject {} "
                    "is still unreaped and is itself the waitable child; a "
                    "further waitable child the census missed is possible but "
                    "unproven, and any such child is NOT signalled "
                    "(D-385-CURRENT-CHILD), left as the disclosed "
                    "orphan-escape residual".format(subject))
            raise ChildStatusUnavailable(
                "descendant cleanup deadline: ECHILD not observed and the "
                "/proc census named no adopted descendant: a waitable child "
                "remains whose identity the census missed; it is NOT "
                "signalled (D-385-CURRENT-CHILD), left as the disclosed "
                "orphan-escape residual")
        time.sleep(0.005)


# array is required: socket.send_fds/recv_fds cold-import it inside their own
# bodies, so importing socket alone does not load it (QA19 F4).
_FIXTURE_GUARDIAN_MODULES = (
    "array", "ctypes", "errno", "gc", "json", "pathlib", "resource", "select",
    "signal", "socket", "time", "traceback")


def _fixture_preload():
    """Import, in the caller and BEFORE any fork, every module the guardian tree's
    function-local imports touch. Only the launcher thread survives into the guardian,
    so a cold post-fork import could block forever on an import or module lock some
    OTHER caller thread held at fork time; preloading makes every guardian-side import
    a lock-free sys.modules hit. The remaining documented restriction is thunk-side
    only: a subject callable must not depend on reacquiring locks (including a cold
    import's module lock) that caller threads hold at call time (QA18 codex F5)."""
    import importlib
    for name in _FIXTURE_GUARDIAN_MODULES:
        importlib.import_module(name)


def _fixture_mask_available():
    """The masked ownership/collection design (fix 2w) requires per-thread
    signal masking; without it construction fails CLOSED (an unmasked
    close()/poll() sequence is never run)."""
    import signal
    return hasattr(signal, "pthread_sigmask")


def _fixture_mask_cancellation():
    """Block the cancellation signals (SIGINT, SIGTERM) on the calling thread
    for one ownership/collection sequence. The kernel keeps a signal raised
    while every thread blocks it pending and delivers it only at the caller's
    SIG_SETMASK restore, so the sequence always completes (or refuses loudly)
    BEFORE the cancellation lands; the interpreter then raises the
    KeyboardInterrupt at the restore's own bytecode boundary -- INSIDE the
    caller's finally (QA21 claude F4) -- so close()/poll() raise the interrupt
    themselves, with any in-flight refusal preserved as the interrupt's
    context. Callers capture the prior mask by QUERY before their try and
    call this as the try's FIRST statement, so an exception during the
    installation itself still restores exactly (QA21 claude F3 / codex F1 /
    gemini F1: the block syscall can land and the interpreter raise before
    the return value reaches the caller). Outside this guarantee (the
    retained BaseException funnels and the subject-side protections are the
    backstop): a signal whose interpreter-level flag tripped before the mask
    landed, a PROCESS-directed signal delivered to a CALLER-created thread
    that leaves the pair unblocked (the layer's own launcher is created with
    the pair blocked, so single-threaded callers are airtight, QA21 claude
    F1), non-signal asynchronous exceptions, and other signals with raising
    handlers."""
    import signal
    return signal.pthread_sigmask(
        signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})


def _fixture_abort_launch(fixture):
    """Construction failed or was cancelled mid-launch (the object never reached its
    caller): tell the parked launcher to never fork -- the cancelled flag is read
    before any fork -- and release every construction resource. A launcher already
    past that check remains the owner of its own fork (_launch collects abandoned
    launches), so nothing here can dispose state a live guardian still uses."""
    with fixture._launch_lock:
        fixture._cancelled = True
    fixture._go.set()
    for resource in (fixture.control, fixture.peer, getattr(fixture, "report", None)):
        if resource is not None:
            try:
                resource.close()
            except OSError:
                pass


class _FixtureProcess:
    """Shared fork/exec tree owner. No caller signal state is borrowed.

    The caller owns a guardian; the guardian alone launches/owns the subject tree.
    The subject is a CALLABLE given at construction and runs only in the subject
    process: start() is parent-only and never returns in a child, so subject-side
    control flow can never fall into caller cleanup. READY/GO separates startup
    (no subject exists, so killing the guardian is safe) from supervision
    (control-channel EOF requests tree cleanup). The guardian records status only
    AFTER tree cleanup reaches ECHILD. Its subreaper flag is private; the caller's
    SIGCHLD, masks, timers and subreaper flag stay untouched.

    Descriptor contract (allowlist, no registry): the guardian's first act is to
    close every inherited descriptor except stdio, its own control peer and
    receipt, and the caller-declared keep_fds, so the guardian and its subject see
    ONLY stdio plus declared descriptors. An undeclared caller fd is a loud
    deterministic refusal (EBADF in the subject), never a sometimes-working leak,
    and a sibling call's endpoints -- constructed or still under construction --
    can never survive into an unrelated guardian.

    Launch ownership: the guardian is forked on a private launcher thread that
    exists from CONSTRUCTION, parked until start() releases it, so start() and
    close() only flip events: no caller-side bytecode boundary -- including the
    inside of threading.Thread.start -- can lose a fork or leave a thread that
    close() cannot decide about. CPython raises asynchronous signal exceptions
    (e.g. a real SIGINT's KeyboardInterrupt) only in the MAIN thread, so the
    launcher always records its fork; close() never joins the launcher, it waits
    on the launcher's completion event and decides by the recorded ownership. A
    cancellation at ANY caller-side point therefore either prevents the fork
    (the cancelled flag is read before forking), reaps the guardian to ECHILD,
    or -- if the launch never completes inside the bounded budget -- refuses
    loudly and ABANDONS the launch to the launcher, which then collects its own
    fork; construction resources are never disposed while the launcher can still
    use them. A failed construction (e.g. thread exhaustion) aborts the launch,
    releases every resource, and surfaces the original error. Cancellation
    masking (fix 2w/2x): close() runs its ENTIRE ownership decision, transfer,
    collection and cleanup sequence -- and poll() its collection-to-recording
    step -- with SIGINT and SIGTERM masked on the calling thread
    (pthread_sigmask), and the LAUNCHER is created with the same pair blocked
    (a thread inherits its creator's mask; the guardian undoes exactly that
    block so the subject sees the caller's original mask), so with a
    single-threaded caller EVERY thread blocks the pair during the sequence
    and even a PROCESS-directed cancellation -- the real Ctrl-C delivery
    shape, which the kernel may hand to ANY thread with it unblocked -- stays
    kernel-pending (QA19 F2, QA20, QA21 claude F1). The prior mask is
    captured by QUERY before the restoring try and the block is the try's
    FIRST statement, restored exactly in the finally (QA21 claude F3): a
    cancellation landing before the block installs disposes NOTHING, not
    even the thread's signal state, and close() can simply run again. The
    pending cancellation is delivered AT the restore, inside close()/poll()'s
    own finally (QA21 claude F4): close() then raises the KeyboardInterrupt
    itself, with any in-flight refusal preserved on the exception chain
    (callers walk __context__). The deferral is BOUNDED (QA21 gemini F3, an
    accepted trade-off: guaranteed ownership and cleanup over immediate
    termination): every masked wait carries its own deadline, so a pending
    cancellation -- including a job supervisor's SIGTERM -- is delayed at
    most the bounded cleanup budget (worst case about two grace periods plus
    the escalation grace), never indefinitely. A host without pthread_sigmask
    REFUSES construction (fail closed, never an unmasked run). Residuals, covered by
    the retained BaseException funnels (and the subject-side ack EOF and
    pdeathsig) as backstops: a signal whose interpreter-level flag tripped
    before the mask landed; a PROCESS-directed signal delivered to a
    CALLER-created thread that leaves the pair unblocked, which the
    interpreter then raises inside the masked sequence (the layer cannot
    mask threads it does not own); and non-signal asynchronous exceptions,
    which no mask can stop. The funnels leave the same owner: an
    unrecorded launch is abandoned to the launcher UNDER THE LAUNCH LOCK, a
    recorded one keeps close(), which finishes collecting before re-raising;
    one landing inside the collection sends the licensed signals -- the
    receipt-identified subject through its held pidfd and the guardian this
    layer forked -- and bounded-reaps the guardian before propagating; the
    subject's own descendants hold no handoff, so they are NOT signalled but
    named and left (D-385-CURRENT-CHILD).

    Locks: only the launcher thread survives into the guardian, whose
    dependencies are preloaded at construction, so guardian-side imports take no
    locks. Documented thunk restriction: a subject callable must not depend on
    reacquiring a lock (including a cold import's module lock) that a caller
    thread held at call time; such a thunk deadlocks in the child and is
    collected as a loud TIMEOUT, never silently.

    Escalation: after forking the subject the guardian delivers a (pid, pidfd)
    receipt on the control socket and only THEN releases the subject through an
    acknowledgment pipe: no subject runs user code or creates descendants
    without a delivered receipt, and a subject whose guardian dies before the
    acknowledgment (EOF), or whose parent is no longer the expected guardian
    when pdeathsig is armed, refuses to run at all. close() collects the
    buffered receipt before closing that socket. A guardian that cannot be
    collected within the bounded cleanup budget is frozen (SIGSTOP via its
    pidfd, so a live guardian's pid/pgid cannot be recycled), then the
    receipt-identified subject's group is CENSUSED under guardian ownership
    (fix 2y): while the frozen guardian lives it is the tree's subreaper,
    which pins the pgid number while /proc is read. The census OBSERVES;
    delivery is licensed per member and ONLY by a pidfd the member's
    FORKING PARENT opened and handed off over SCM_RIGHTS
    (D-385-PIDFD-HANDOFF; the retired ancestry walk licensed delivery by
    /proc parent chains and is gone) -- NEVER a locally opened pidfd, NEVER
    a numeric killpg and NEVER a census anchored on the subject's bare
    numeric pid (QA20 claude F1, QA21 codex F3, fix 2y codex F1: only
    unreapability pins a pid or pgid NUMBER against reuse). The runtime
    escalation holds NO member handoffs -- the subject's forks are its own
    -- so every observed live member is SKIPPED and NAMED in the refusal,
    the fail-closed disclosure, and those descendants join the documented
    orphan-escape residual. Handed-off members (self-test fixtures thread
    them) die BEFORE the leader (the census excludes the leader; its
    held-pidfd SIGKILL runs last), so no member is stranded by its leader's
    death, and every observed member the census could not address is
    accounted (fix 2y codex F2; a member no census observed -- the
    disclosed fork-race and pid-wraparound residual -- can be neither
    addressed nor accounted, per the ruling PD-335-TREE-CLAIM-STALL). The
    "tree" claim then rests on OBSERVATION, never on the kill sends alone
    (fix 2z; maintainer ruling PD-335-TREE-CLAIM-STALL):
    while the guardian stays frozen, a bounded verification census must
    observe NO live, signalable group member in TWO CONSECUTIVE clean
    passes -- an observation, never a proof (a continuously forking chain
    or pid wraparound can evade it); a member still live at the bound (e.g.
    forked past the kill snapshot), an unreadable /proc entry (never proof
    of exit; a possible member, accounted apart from established members),
    or a leader SIGKILL failing with anything but ProcessLookupError
    downgrades the outcome to
    the named partial accounting. Then the guardian itself is SIGKILLed. On ANY guardian
    failure, escalated or not -- including a failure FIRST collected by
    poll(), which kills the subject AT COLLECTION TIME and records the
    failure for close() to prove and re-raise (QA19 F1), an UNRESOLVED
    collection (guardian reaped, status never validated, no failure
    recorded: treated as a failure of close()'s own, QA20 codex F1), and a
    LOST-OWNERSHIP collection (the guardian disappeared without this
    close()'s reap, fix 2y claude Finding 2) -- a receipt-identified subject
    is killed and its disappearance proven on its pidfd or refused loudly;
    on every one of those guardian-DEAD paths the kill is SUBJECT-ONLY: the
    dead guardian owns no orphans, so NO member census runs, NO new pidfds
    are opened, and only the already-held subject pidfd is signalled (fix
    2y, D2).

    Refusal wording, class-wide (each names ONLY the steps actually taken,
    QA19 F3 / QA20 claude F3 / fix 2y codex F2):

      kill state ran               refusal names
      ---------------------------  ------------------------------------------
      no receipt                   subject cleanup INCOMPLETE
      pid-only receipt (no pidfd)  subject cleanup UNVERIFIED (escalated,
                                   poll-collected and lost-ownership paths)
      subject-only (guardian dead  subject exit proven on its pidfd;
      or census unpinned)          descendants, if any, UNADDRESSED
                                   (orphan-escape residual,
                                   pdeathsig-backed)
      "partial" census (skipped    the unaddressed members and the
      or unreadable membership)    unverifiable entries (possible members),
                                   by pid, or "unknown (census unreadable)"
      "tree" (guardian-anchored    "subject tree killed: every observed
      census, every OBSERVED       member addressed, none observed in two
      member addressed, then a     consecutive clean censuses" -- the ONLY
      bounded verification         state that ever claims a killed tree,
      census OBSERVED no live      and even it is worded as an observation
      member, twice                with its residual (a forking chain or
      consecutively)               pid wraparound can evade the censuses),
                                   never a proof

    Documented residuals (D-385-PIDFD-HANDOFF, D-385-CURRENT-CHILD): every
    descendant without a forking-parent handoff survives. Same-group
    members of a WEDGED-guardian escalation are observed, SKIPPED and
    NAMED (the runtime holds no member handoffs); descendants that leave
    the subject's group/session are not even observed; ALL descendants of
    a subject whose guardian is DEAD survive the subject-only kill (no
    process owns the orphans after the subreaper guardian's death, so a
    census there could only trust recyclable numbers and unverifiable
    reparented chains; fix 2y, D2); and the honest guardian's drain kills
    ONLY the subject it forked -- an adopted descendant is reaped if it
    exits within the drain's deadline and otherwise NAMED, not signalled,
    in the drain refusal -- each disclosed where it arises, never claimed
    killed. The subject arms PR_SET_PDEATHSIG(SIGKILL) as partial
    extra coverage, failing closed to these residuals where unavailable.
    Hosts without pidfd degrade escalation to guardian-only with the same
    residuals.

    Parent-side fork callbacks and uninterruptible kernel waits remain unbounded.
    """
    def __init__(self, deadline, keep_fds=(), subject=None):
        import signal
        import socket
        import tempfile
        import threading
        if not _fixture_mask_available():
            # Fail CLOSED (fix 2w): without pthread_sigmask the ownership and
            # cleanup sequences would run unmasked and a cancellation could
            # land between their steps. Documented residual: such hosts cannot
            # run fixtures at all.
            raise ChildStatusUnavailable(
                "cannot mask cancellation signals (signal.pthread_sigmask "
                "unavailable): refusing fixture launch")
        self.deadline = deadline
        self.pid = self.pidfd = self.status = None
        self.subject_pid = self.subject_pidfd = None
        self.armed = self.collected = self.timed_out = False
        # Separate explicit states (QA20 codex F1): `collected` says only that
        # the GUARDIAN was reaped; `unresolved` marks an armed collection whose
        # receipt was never validated with no failure recorded (close() still
        # treats it as a failure); `cleaned` says the collected guardian's
        # subject-cleanup obligation was discharged (validated clean status,
        # or kill plus disappearance proof). `_subject_kill` records the one
        # receipt kill's accounting state: "tree" (guardian-anchored census,
        # every OBSERVED member addressed, none observed in two consecutive
        # clean verification censuses -- an observation, never a proof),
        # "partial"
        # (census ran; the (members, unverifiable-entries) it could not
        # address are in `_subject_skipped`, None when the census was
        # unreadable), or "subject-only" (no guardian ownership: the
        # held-pidfd subject kill only, descendants disclosed, fix 2y).
        self.unresolved = False
        self.cleaned = False
        self._subject_kill = None
        self._subject_skipped = None
        # keep_fds: descriptors the guardian and its subject still need (e.g.
        # run_bounded's pipe). Everything else inherited is closed in the guardian.
        self.keep_fds = tuple(keep_fds)
        self.subject = subject
        self._launcher = None
        self._launch_error = None
        # The recorded guardian failure, set at COLLECTION time by whichever
        # collector reads the failed report first (QA19 F1); close() re-raises
        # it after addressing the receipt-identified subject.
        self._failure = None
        self._launch_lock = threading.Lock()
        self._go = threading.Event()          # start()/close() release the launcher
        self._launched = threading.Event()    # set by the launcher on every path
        self._cancelled = self._abandoned = False
        # Guardian dependencies are imported HERE, pre-fork, so every
        # guardian-side function-local import is a lock-free sys.modules hit.
        _fixture_preload()
        self.control, self.peer = socket.socketpair()  # non-inheritable across exec
        try:
            self.report = tempfile.TemporaryFile()
            # The launcher exists from construction, parked until released:
            # start() only sets an event, so no caller-side bytecode boundary
            # sits between creating the launcher and owning what it forks. It
            # is STARTED with {SIGINT, SIGTERM} blocked -- a thread inherits
            # its creator's mask -- so the layer never adds a thread the
            # kernel could pick for a PROCESS-directed cancellation (QA21
            # claude F1): with a single-threaded caller, every thread then
            # blocks the pair inside close()/poll() and a real Ctrl-C stays
            # kernel-pending until the restore. The guardian undoes exactly
            # this block (`_launch_masked`), so the subject still sees the
            # caller's original mask.
            prior = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            self._launch_masked = {signal.SIGINT, signal.SIGTERM} - prior
            try:
                _fixture_mask_cancellation()
                launcher = threading.Thread(
                    target=self._launch, name="opf-fixture-launcher", daemon=True)
                launcher.start()
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, prior)
            self._launcher = launcher
        except BaseException:
            _fixture_abort_launch(self)
            raise

    def start(self):
        try:
            return self._start()
        except BaseException:
            self.close()
            raise

    def _launch(self):
        """Launcher-thread body: parked from construction, released by start() (fork)
        or close() (refuse). Runs only on the private launcher thread, where CPython
        never raises asynchronous signal exceptions, so no bytecode boundary here can
        lose the fork result between the fork and its store -- by interpreter
        construction, not by statement packing. A launch close() gave up on is
        collected HERE: the launcher is then the fork's last owner. The park is
        the one unbounded wait of this class (opf.py's _RUNTIME_WAIT_EXEMPT, QA22):
        it holds only this daemon thread, and start() waits for the launch through
        a bounded _launched.wait, so no caller ever waits on the park itself."""
        try:
            self._go.wait()
            with self._launch_lock:
                cancelled = self._cancelled
            if cancelled:
                raise ChildStatusUnavailable("fixture launch cancelled before fork")
            self._launch_fork()
        except BaseException as exc:  # parent-side only; surfaced by _start
            self._launch_error = exc
        finally:
            with self._launch_lock:
                self._launched.set()
                abandoned = self._abandoned
        if abandoned:
            self._collect_abandoned()

    def _launch_fork(self):
        import os
        pid = os.fork()
        if pid == 0:
            self._guardian()  # never returns: every guardian path ends in os._exit
        self.pid = pid
        self.pidfd = _fixture_pidfd(pid)

    def _collect_abandoned(self):
        """close() already refused with ownership-unknown and disposed nothing: kill
        and reap any guardian this launch produced (it is pre-GO, so no subject can
        exist), then release the construction resources close() left untouched."""
        import os
        import signal
        import time
        try:
            if self.pid is not None and not self.collected:
                _fixture_signal(self.pid, signal.SIGKILL, self.pidfd,
                                forked=True)
                # A bounded reap (QA22), never a blocking waitpid: a guardian
                # the kill did not reach (or that will not die) is left
                # uncollected after the cleanup grace instead of holding
                # this thread on it.
                if self._await_exit(time.monotonic() + _FIXTURE_CLEANUP_GRACE) \
                        and os.waitpid(self.pid, os.WNOHANG)[0] == self.pid:
                    self.collected = True
        except OSError:
            pass
        finally:
            for resource in (self.control, self.peer, self.report):
                try:
                    resource.close()
                except OSError:
                    pass
            for name in ("pidfd", "subject_pidfd"):
                fd = getattr(self, name)
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    setattr(self, name, None)

    def _await_exit(self, until):
        """Wait, WITHOUT reaping, until this launch's own guardian has exited or
        the monotonic `until` has passed (QA22): a WNOHANG poll, never a blocking
        waitid. True when it has exited; an OSError propagates."""
        import os
        import time
        pause = 0.001
        while os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is None:
            left = until - time.monotonic()
            if left <= 0.0:
                return False
            time.sleep(min(pause, left, 0.05))
            pause = min(pause * 2.0, 0.05)
        return True

    def _start(self):
        import os
        import select
        import signal
        import sys
        import time
        if sys.platform != "linux" or not all(
                hasattr(os, name) for name in ("fork", "waitid", "WNOWAIT", "P_ALL")):
            raise ChildStatusUnavailable("Linux fork/waitid/subreaping required")
        if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
            raise ChildStatusUnavailable("unowned SIGCHLD disposition")
        if not callable(self.subject):
            raise ChildStatusUnavailable("fixture subject must be a callable")
        # Release the launcher parked since construction. Bounded wait: a fork
        # wedged in the kernel or an at-fork hook is a cannot-evaluate; close()
        # re-checks the same completion event before deciding.
        self._go.set()
        if not self._launched.wait(max(self.deadline - time.monotonic(),
                                       _FIXTURE_CLEANUP_GRACE)):
            raise TimeoutError("fixture launch deadline")
        if self._launch_error is not None:
            raise self._launch_error
        pid = self.pid
        self.peer.close()
        self.control.setblocking(False)
        poller = select.poll()
        poller.register(self.control, select.POLLIN | select.POLLHUP | select.POLLERR)
        while time.monotonic() < self.deadline:
            if poller.poll(5):
                if os.read(self.control.fileno(), 1) != b"R":
                    # The reap of the failed guardian and its `collected` mark
                    # are ONE step with respect to the caller's cancellation
                    # signals (QA17 claude m1), as in _fixture_drain: a
                    # SIGINT/SIGTERM landing between them stays pending until
                    # the restore below, so close() never treats a reaped
                    # number as an uncollected guardian. The exit itself is
                    # awaited first WITHOUT reaping and unmasked, so a
                    # cancellation can still interrupt that wait. The reap is a
                    # WNOHANG poll (QA18 claude m2): a 0 result is an unexpected PID.
                    # The exit wait is bounded by the fixture deadline (QA22): a
                    # failed guardian that never exits (stopped, wedged) is this
                    # launch's OWN pre-GO fork, with no subject yet, so it is
                    # SIGKILLed and must then exit within the cleanup grace; its
                    # receipt then reports the kill.
                    try:
                        if not self._await_exit(self.deadline):
                            _fixture_signal(self.pid, signal.SIGKILL, self.pidfd,
                                            forked=True)
                            if not self._await_exit(time.monotonic() + _FIXTURE_CLEANUP_GRACE):
                                raise ChildStatusUnavailable(
                                    "guardian failed before READY and did not exit "
                                    "after SIGKILL")
                    except OSError as exc:
                        raise ChildStatusUnavailable(
                            "cannot collect fixture status: " + str(exc)) from exc
                    prior = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                    try:
                        _fixture_mask_cancellation()
                        waited, raw = _fixture_wait(self.pid, os.WNOHANG)
                        if waited == self.pid:
                            self.collected = True
                    finally:
                        signal.pthread_sigmask(signal.SIG_SETMASK, prior)
                    if waited != self.pid:
                        raise ChildStatusUnavailable("unexpected startup wait PID")
                    self._read_report(raw)
                    raise ChildStatusUnavailable("guardian failed before READY")
                if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
                    raise ChildStatusUnavailable("SIGCHLD changed during launch")
                self.armed = True
                os.write(self.control.fileno(), b"G")
                return pid
        self.timed_out = True
        raise TimeoutError("fixture startup deadline")

    def _guardian(self):
        """Guardian process body (the forked child's only thread); never returns."""
        import gc
        import json
        import os
        import signal
        import time
        subject = subject_fd = cleanup_deadline = None
        subject_ref = None
        stage = "startup"
        try:
            # The launcher forked this guardian with the cancellation signals
            # blocked (a construction-time inheritance, see __init__): undo
            # exactly that block, so the guardian tree -- and the SUBJECT it
            # forks -- sees the caller's original SIGINT/SIGTERM disposition.
            # Guardian cleanup itself is driven by pidfd SIGSTOP/SIGKILL,
            # which no mask stops.
            signal.pthread_sigmask(signal.SIG_UNBLOCK, self._launch_masked)
            # The forked heap is the caller's: a collection here could run
            # caller-object finalizers whose fd closes would hit numbers this
            # guardian legitimately reuses after the sweep below. Keep collection
            # off for the guardian's short life.
            gc.disable()
            # The whole descriptor contract, applied unconditionally: only stdio,
            # this call's peer + receipt, and the caller-declared keep_fds survive
            # into the guardian and its subject.
            _fixture_close_all_except(
                set((self.peer.fileno(), self.report.fileno(), *self.keep_fds)))
            os.setpgid(0, 0)  # no group signal can ever address the caller's group
            _fixture_subreaper()
            os.write(self.peer.fileno(), b"R")
            if os.read(self.peer.fileno(), 1) != b"G":
                os._exit(0)  # cancelled before GO: no subject exists
            guardian = os.getpid()
            ack_r, ack_w = os.pipe()
            subject = os.fork()
            if subject == 0:
                try:
                    self.peer.close()
                    self.report.close()
                    os.close(ack_w)
                    _fixture_pdeathsig()
                    _fixture_check_parent(guardian)
                    os.setsid()  # before any subject code, and before any exec
                    _fixture_await_ack(ack_r)
                    _fixture_enable_gc()
                    self.subject()
                    os._exit(0)
                except BaseException:
                    # This is the subject, not the guardian: its report is
                    # already closed. Preserve the bootstrap cause on fd 2.
                    import traceback
                    try:
                        with os.fdopen(os.dup(2), "w") as diagnostic:
                            traceback.print_exc(file=diagnostic)
                    finally:
                        os._exit(125)
            os.close(ack_r)
            # One holder for the subject's whole life: the failure-path
            # retry below shares it, so a subject reaped by the first drain
            # is never signalled by number again (QA15 codex blocker).
            subject_ref = [subject]
            stage = "subject-receipt"
            subject_fd = _fixture_pidfd(subject)
            _fixture_send_subject(self.peer, subject, subject_fd)
            _fixture_ack_subject(ack_w)
            self.peer.setblocking(False)
            timed_out = False
            stage = "supervision"
            while True:
                ended = os.waitid(os.P_PID, subject, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                # Deadline expiry is authoritative BEFORE accepting
                # completion: the clock is sampled AFTER waitid, so only a
                # pre-expiry sample proves the exit preceded the deadline.
                # An exit first observed after expiry is recorded as a
                # timeout, whatever order the processes were scheduled in.
                # Cleanup below keeps its own separate budget.
                if time.monotonic() >= self.deadline:
                    timed_out = True
                    break
                if ended is not None:
                    break
                try:
                    if not os.read(self.peer.fileno(), 1):
                        break
                    raise ChildStatusUnavailable("unexpected guardian control byte")
                except BlockingIOError:
                    pass
                time.sleep(0.005)
            stage = "drain"
            cleanup_deadline = _fixture_cleanup_deadline(self.deadline)
            status = _fixture_drain(subject, subject_fd, deadline=cleanup_deadline,
                                    subject_ref=subject_ref)
            subject = None
            if subject_fd is not None:
                fd, subject_fd = subject_fd, None         # ownership first: a failed close is never
                os.close(fd)                              # closed again by the cleanup (P1, #378)
            if status is None:
                raise ChildStatusUnavailable("subject status unavailable")
            stage = "receipt"
            self.report.write(json.dumps([status, timed_out]).encode("ascii"))
            self.report.flush()
            os._exit(0)
        except BaseException as exc:
            def detail(error):
                try:
                    message = str(error)[:512]
                except BaseException:
                    message = "<exception message unavailable>"
                try:
                    number = getattr(error, "errno", None)
                except BaseException:
                    number = None
                return {"type": type(error).__name__[:128],
                        "errno": number if type(number) is int else None, "message": message}

            failure = {"stage": stage, "error": detail(exc), "cleanup": "pending"}

            def record_failure():
                try:
                    self.report.seek(0)
                    self.report.truncate()
                    self.report.write(json.dumps(failure).encode("ascii"))
                    self.report.flush()
                except BaseException:
                    # A broken report channel cannot certify anything. The
                    # caller still reports the raw nonzero guardian status.
                    pass

            record_failure()  # preserve the cause even if cleanup cannot finish
            try:
                if subject is not None:
                    if cleanup_deadline is None:
                        cleanup_deadline = _fixture_cleanup_deadline(self.deadline)
                    _fixture_drain(subject, subject_fd, deadline=cleanup_deadline,
                                   subject_ref=subject_ref)
                failure["cleanup"] = "ECHILD" if subject is not None else "no subject"
            except BaseException as cleanup_exc:
                failure["cleanup"] = detail(cleanup_exc)
            finally:
                record_failure()
                try:
                    if subject_fd is not None:
                        os.close(subject_fd)
                finally:
                    os._exit(125)

    def poll(self):
        import os
        import signal
        if self.collected:
            return self.status
        # The whole collection-to-failure-recording step runs MASKED (fix 2w):
        # a cancellation can no longer land between reaping the guardian and
        # recording the outcome (QA20 codex F1 second gap); it stays pending
        # and is delivered AT the restore below, inside this finally. The
        # prior mask is captured by QUERY before the try and the block is the
        # try's FIRST statement (QA21 claude F3 / codex F1 / gemini F1), so
        # an exception raised during the installation itself still reaches
        # the restoring finally and the caller's mask can never leak blocked.
        # `unresolved` is written BEFORE `collected` (QA21 claude F2): an
        # asynchronous exception between the two writes leaves close() OWNING
        # the collection -- its own reap then refuses loudly ("guardian
        # ownership lost") -- never the half-recorded silent state (collected
        # recorded, unresolved lost, no failure) the pre-fix order allowed. A
        # synchronous failure in the window still leaves `unresolved` set,
        # which close() treats as a failure of its own.
        prior = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        try:
            _fixture_mask_cancellation()
            waited, raw = _fixture_wait(self.pid, os.WNOHANG)
            if waited == 0:
                return None
            if waited != self.pid:
                raise ChildStatusUnavailable("unexpected guardian wait PID")
            if self.armed:
                self.unresolved = True
            self.collected = True
            try:
                self._read_report(raw)
            except ChildStatusUnavailable:
                # Address the receipt-identified subject AT COLLECTION time.
                # The guardian is DEAD here, so the kill is SUBJECT-ONLY
                # through the held receipt pidfd (fix 2y, D2: no ownership
                # licenses a member census after guardian death); close()
                # still runs the disappearance proof and re-raises the
                # recorded failure.
                self._address_failed_subject()
                raise
            return self.status
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, prior)

    def _record_failure(self, failure):
        """Record a supervision failure at COLLECTION time, whichever collector
        observed it first: close() must address the receipt-identified subject
        and re-raise the recorded failure even when poll() or start() was the
        collector that read the failed report (QA19 F1). A recorded failure
        is a RESOLVED collection; the subject-cleanup obligation it creates is
        tracked by `cleaned`, never by `unresolved`."""
        self._failure = failure
        self.unresolved = False
        return failure

    def _read_report(self, raw):
        import json
        import os
        termination = "raw wait status={}, exitcode={}".format(raw, os.waitstatus_to_exitcode(raw))
        self.report.seek(0)
        payload = self.report.read(8193)
        if not os.WIFEXITED(raw) or os.WEXITSTATUS(raw) != 0:
            receipt = payload[:8192].decode("ascii", errors="backslashreplace")
            raise self._record_failure(ChildStatusUnavailable(
                "fixture guardian failed: {}; receipt={}".format(termination, receipt or "<missing>")))
        try:
            if len(payload) > 8192:
                raise ValueError("oversized receipt")
            result = json.loads(payload)
        except (ValueError, UnicodeError) as exc:
            raise self._record_failure(ChildStatusUnavailable(
                "missing tree-cleanup receipt; " + termination)) from exc
        if (not isinstance(result, list) or len(result) != 2
                or type(result[0]) is not int or type(result[1]) is not bool):
            raise self._record_failure(ChildStatusUnavailable(
                "malformed tree-cleanup receipt; " + termination))
        self.status, self.timed_out = result
        self.unresolved = False

    def _recv_subject(self):
        """Collect the buffered subject receipt, if any. It must be read BEFORE the
        control socket is closed: closing that socket is what requests tree cleanup,
        and the receipt stays buffered in it even after the guardian's death."""
        import os
        import select
        import socket
        if not self.armed or self.subject_pid is not None:
            return
        try:
            # select.poll, never select.select: a control socket at or above
            # FD_SETSIZE (1024) must still deliver its buffered receipt
            # instead of a swallowed ValueError silently losing the subject
            # (fix 2z, claude F2 / gemini F4).
            poller = select.poll()
            poller.register(self.control, select.POLLIN)
            if poller.poll(500):
                msg, fds, _flags, _addr = socket.recv_fds(self.control, 32, 1)
                if msg:
                    self.subject_pid = int(msg)
                if fds:
                    self.subject_pidfd = fds[0]
                    for extra in fds[1:]:
                        os.close(extra)
        except (OSError, ValueError) as exc:
            # Narrowed (QA21 gemini F2): TimeoutError/InterruptedError are
            # OSError subclasses carrying deadline/cancellation semantics a
            # receipt read must never swallow; only genuine I/O or parse
            # failures degrade to the documented no-receipt path.
            if isinstance(exc, (TimeoutError, InterruptedError)):
                raise

    def _address_failed_subject(self):
        """Kill the receipt-identified subject of a FAILED, UNRESOLVED or
        LOST-OWNERSHIP guardian. The guardian is DEAD on every path here, so
        no process owns the tree's orphans: the kill is SUBJECT-ONLY -- one
        SIGSTOP+SIGKILL through the already-held subject pidfd
        (identity-safe), NO member census, NO new pidfds (fix 2y, codex
        F1/F2 under D2: after guardian death a census could only trust
        recyclable numbers and reparented chains it cannot verify).
        Descendants -- same-group or not: no census runs at all -- are the
        documented orphan-escape residual (pdeathsig-backed); callers
        DISCLOSE them as unaddressed and never report a killed tree here.
        Idempotent -- the kill is sent once, whichever collector gets here
        first. Callers still prove the
        subject's disappearance on its pidfd before re-raising."""
        self._recv_subject()
        if self.subject_pidfd is not None and self._subject_kill is None:
            outcome = _fixture_escalate_subject(self.subject_pid, self.subject_pidfd)
            if outcome is not None:
                self._subject_kill, self._subject_skipped = outcome

    def _escalate(self):
        """Freeze first, then kill: SIGSTOP the guardian via its pidfd (a
        frozen guardian exists unreaped and cannot reap, so the group it
        subreaper-owns stays pinned for the census), kill the
        receipt-identified subject under that guardian ownership (the census
        OBSERVES the group; member delivery needs a forking-parent pidfd
        handoff, which the runtime has none of, so observed live members are
        skipped and NAMED -- D-385-PIDFD-HANDOFF fail-closed -- never a
        numeric killpg, and the "tree" claim licensed only by the bounded
        post-kill verification census's two consecutive clean passes -- an
        observation, never a proof -- fix 2z / round 24), then SIGKILL the guardian
        itself; the caller's bounded reap loop collects it and close() then
        proves the subject's exit. A freeze that FAILED (guardian gone)
        licenses NO census at all: the kill degrades to subject-only through
        the held receipt pidfd (fix 2y, D2). Exception safety (fix 2z, codex
        BLOCKER 3; round 24): both freezes run INSIDE the kill protection,
        so a raising freeze never skips a kill; the guardian SIGKILL runs
        even if the freeze or the subject cleanup raises -- and if the
        ownership-checked kill helper itself fails, the guardian is still
        SIGKILLed directly through its held, identity-safe pidfd, where a
        cancellation propagates with the helper's failure chained as its
        context (round 24, codex BLOCKER 2), and ONE shared exception
        boundary (_cleanup_boundary, fix 6) spans that whole cleanup,
        direct backstop included -- and a cancellation the kill helper
        ITSELF raises becomes the pending cancellation for the direct
        backstop (fix 7, QA28 codex BLOCKER 1), so a backstop failure
        attaches beneath it, never over it: a cancellation ALREADY propagating into
        it stays the OUTWARD exception, never displaced, its own
        pre-existing chain kept intact, and every later failure -- helper
        or backstop, ordinary or cancellation -- is kept REACHABLE beneath
        it (fix 14, QA35 codex MAJOR: an ordinary backstop delivery fault
        was discarded outright; only a backstop ProcessLookupError stays
        unrecorded, proving the guardian already exited): attached as its
        __cause__ when that slot is free, else
        appended at the tail of its existing cause/context chain, else
        (only when no acyclic attachment exists) named in a note, with
        every diagnostic computed inside the boundary's protection so a
        raising __repr__ never escapes over it (QA25/QA26 codex; QA27
        codex BLOCKER 1/2 closed the site class at the boundary) -- and an
        ordinary census
        failure that escaped the escalation helper records the kill that
        DID run ("partial", members unknown; the helper's own protection
        ran the held-pidfd SIGKILL, whose delivery is only ever worded as
        attempted), so any later refusal names exactly what ran, while a
        failure BEFORE the subject cleanup records nothing: the cleanup
        never ran and the retry still owns it. The original exception still
        propagates. Cancellation semantics record nothing: the interrupt
        owner re-sends the idempotent receipt kill -- and the recording
        tier is harmonized with the boundary's pending set (fix 7, QA28
        claude MINOR 3): TimeoutError, InterruptedError and
        KeyboardInterrupt all record nothing."""
        import signal
        frozen = False
        pending = None

        def kill_guardian():
            # The guardian SIGKILL is exception-safe (fix 2z, codex
            # BLOCKER 3): a raising subject cleanup never strands a frozen
            # guardian; _cleanup_boundary spans this WHOLE step, direct
            # backstop included (QA26 codex; fix 6).
            try:
                _fixture_signal(self.pid, signal.SIGKILL, self.pidfd,
                                forked=True)
            except BaseException as helper_exc:
                # Even the ownership-checked helper failing (e.g. the
                # same census fault reaching its own group census) must
                # not strand the frozen guardian: its held pidfd is
                # identity-safe, SIGKILL it directly, then let the
                # failure propagate. The backstop is a LATER cleanup
                # step, so it runs through its own boundary (fix 7, QA28
                # codex BLOCKER 1): a cancellation the helper ITSELF
                # raised is pending for the backstop and can never be
                # replaced by a backstop failure; with an ordinary
                # helper failure nothing is pending at this boundary, so
                # a backstop cancellation propagates over it, carrying
                # it as its __context__ (round 24, codex BLOCKER 2), and
                # the OUTER boundary still keeps a cancellation already
                # propagating into the finally outward (QA26 codex).
                def direct_backstop():
                    if self.pidfd is None:
                        return
                    try:
                        signal.pidfd_send_signal(self.pidfd,
                                                 signal.SIGKILL)
                    except BaseException as exc:
                        # ONE explicit cancellation tier (fix 8, QA29
                        # gemini MAJOR): TimeoutError, InterruptedError
                        # and KeyboardInterrupt propagate identically
                        # from the backstop send, into the boundary
                        # that runs this step.
                        if isinstance(exc, _PENDING_CANCELLATIONS):
                            raise
                        if not isinstance(exc, OSError):
                            raise
                        if (not isinstance(exc, ProcessLookupError)
                                and (isinstance(helper_exc,
                                                _PENDING_CANCELLATIONS)
                                     or isinstance(
                                         pending,
                                         _PENDING_CANCELLATIONS))):
                            # fix 14 (QA35 codex MAJOR): with a
                            # cancellation pending -- the helper's own,
                            # pending at THIS boundary, or one already
                            # propagating into the enclosing finally --
                            # a discarded delivery fault would vanish
                            # from that cancellation's chain; re-raise
                            # it so a boundary keeps it reachable
                            # beneath the outward cancellation. A
                            # ProcessLookupError is no failure to keep:
                            # it proves the guardian already exited.
                            raise
                        # An ordinary delivery failure is out of moves:
                        # the helper's own failure propagates below.

                _cleanup_boundary(
                    helper_exc if isinstance(helper_exc,
                                             _PENDING_CANCELLATIONS)
                    else None,
                    direct_backstop, "direct guardian SIGKILL backstop")
                raise

        try:
            # The freeze runs INSIDE the guardian-kill protection (round 24,
            # codex boundary): an exception here must still reach the
            # finally's guardian SIGKILL -- and it records NOTHING, because
            # the subject cleanup never ran and the retry still owns it.
            if self.pidfd is not None:
                try:
                    signal.pidfd_send_signal(self.pidfd, signal.SIGSTOP)
                    frozen = True  # delivered: the guardian exists unreaped
                except (ProcessLookupError, OSError) as exc:
                    # Narrowed (QA21 gemini F2): deadline/cancellation
                    # semantics propagate; an exited guardian is collected
                    # by the reap loop.
                    if isinstance(exc, (TimeoutError, InterruptedError)):
                        raise
            if self.subject_pidfd is not None and self._subject_kill is None:
                try:
                    outcome = _fixture_escalate_subject(
                        self.subject_pid, self.subject_pidfd,
                        guardian_pid=self.pid if frozen else None)
                except (TimeoutError, InterruptedError, KeyboardInterrupt):
                    # Harmonized with the boundary's pending set (fix 7,
                    # QA28 claude MINOR 3): every cancellation type
                    # records nothing -- the interrupt owner re-sends
                    # the idempotent receipt kill.
                    raise
                except BaseException:
                    # The failure escaped the escalation helper, whose freeze
                    # and census both run inside the subject-SIGKILL
                    # protection (round 24), so the held-pidfd kill DID run:
                    # record it -- census incomplete, members unknown -- so
                    # the refusal names exactly what ran (fix 2z, codex
                    # BLOCKER 3).
                    self._subject_kill, self._subject_skipped = "partial", None
                    raise
                if outcome is not None:
                    self._subject_kill, self._subject_skipped = outcome
        except BaseException as exc:
            # Capture the exception ALREADY propagating into the
            # guardian-kill finally: a pending cancellation must stay the
            # outward exception even when the cleanup below fails (QA25
            # codex; fix 6 routes the whole step through the one shared
            # boundary).
            pending = exc
            raise
        finally:
            _cleanup_boundary(pending, kill_guardian,
                              "guardian-kill cleanup")

    def _abandon_unfinished_launch(self):
        """Decide, under the launch lock, who owns an unfinished launch: if the
        launcher has not recorded its result yet, ABANDON the launch to it (the
        launcher is then the fork's last owner and collects its own fork,
        QA19 F2) and report True; a result recorded in the meantime keeps this
        close() the owner."""
        with self._launch_lock:
            if self._launched.is_set():
                return False
            self._abandoned = True
            return True

    def close(self):
        import signal
        # DESIGN (fix 2w/2x): the ENTIRE ownership decision and transfer, and
        # the whole collection and cleanup sequence (receipt kill,
        # disappearance proof, re-raise preparation), run with the
        # cancellation signals masked on this thread: an asynchronous
        # cancellation can no longer land BETWEEN guarded regions (QA20). The
        # EXACT prior mask is captured by QUERY before the try and the block
        # itself is the try's FIRST statement (QA21 claude F3 / codex F1 /
        # gemini F1), so an exception raised during the installation still
        # restores: a cancellation landing before the block genuinely
        # disposes NOTHING -- not even this thread's signal state -- and this
        # close() can simply run again. A cancellation raised inside the
        # sequence stays kernel-pending and is delivered AT the restore below
        # -- i.e. inside this finally (QA21 claude F4) -- so close() then
        # raises the KeyboardInterrupt itself, with any in-flight refusal
        # preserved on the exception chain (callers walk __context__). The
        # BaseException funnels inside remain the backstop for a signal whose
        # interpreter-level flag tripped before the mask landed, for a
        # PROCESS-directed signal surfaced through a caller-created unblocked
        # thread, and for non-signal asynchronous exceptions, which no mask
        # can stop.
        prior = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        pending = None

        def restore_sigmask():
            signal.pthread_sigmask(signal.SIG_SETMASK, prior)

        try:
            _fixture_mask_cancellation()
            self._close_masked()
        except BaseException as exc:
            # Capture the exception already propagating into the restore
            # (fix 8): the mask restore is module-owned cleanup, routed
            # through the shared boundary, so the KeyboardInterrupt a
            # masked cancellation delivers AT the restore lands inside
            # the boundary -- a cancellation already outward stays
            # outward, and a normal completion still raises the
            # delivered interrupt itself, exactly as before.
            pending = exc
            raise
        finally:
            _cleanup_boundary(pending, restore_sigmask,
                              "cancellation mask restore")

    def _close_masked(self):
        # Coordinated launch lifecycle FIRST, before any resource is disposed. The
        # launcher is never joined (its OS thread state is not what decides); its
        # completion event is set on every executed path, and the cancelled flag
        # is read before any fork, so a cancellation here either prevents a fork
        # that has not happened yet or finds its result recorded below. If the
        # event never fires, the launch is ABANDONED to the launcher, which then
        # collects (or never creates) its own fork; nothing is disposed on that
        # path, because the launcher and its guardian can still use it all. An
        # exception raised INSIDE this coordination -- normally impossible for a
        # masked cancellation, but reachable by a pre-mask interpreter flag or a
        # non-signal asynchronous exception -- must leave the same owner
        # (QA19 F2): the launcher for an unrecorded launch (abandoned under the
        # launch lock before re-raising), or this close(), which finishes
        # collecting before re-raising.
        def release_launcher():
            self._go.set()

        def abandon_unfinished():
            return self._abandon_unfinished_launch()

        def interrupt_collect():
            self._interrupt_collect()

        try:
            self._close_coordinated()
        except ChildStatusUnavailable:
            raise
        except BaseException as exc:
            # Backstop for an exception landing on a coordination boundary the
            # inner guards do not cover (a pre-mask interpreter flag, under the
            # masked design): never escape without an owner. Every step here
            # is module-owned cleanup and routes through the shared boundary
            # (fix 8), so a failing release, abandonment decision or
            # interrupt collection never displaces the cancellation it
            # serves.
            if self._launcher is not None:
                _cleanup_boundary(
                    exc if isinstance(exc, _PENDING_CANCELLATIONS)
                    else None,
                    release_launcher, "parked launcher release")
                if self._abandoned or _cleanup_boundary(
                        exc if isinstance(exc, _PENDING_CANCELLATIONS)
                        else None,
                        abandon_unfinished, "unfinished-launch abandonment"):
                    raise
            _cleanup_boundary(
                exc if isinstance(exc, _PENDING_CANCELLATIONS) else None,
                interrupt_collect, "interrupt-owner collection")
            raise

    def _close_coordinated(self):
        def abandon_unfinished():
            return self._abandon_unfinished_launch()

        def make_refusal():
            return ChildStatusUnavailable(
                "fixture launch did not complete: guardian ownership unknown")

        def finish_close():
            self._finish_close()

        if self._launcher is not None:
            with self._launch_lock:
                self._cancelled = True
                abandoned = self._abandoned
            interrupted = None
            if not abandoned:
                launched = False

                def release_launcher():
                    self._go.set()

                try:
                    self._go.set()
                    launched = self._launched.wait(2 * _FIXTURE_CLEANUP_GRACE)
                except BaseException as exc:  # noqa: BLE001 - ownership survives cancellation
                    # A cancellation in the gap before the release must never
                    # leave the launcher parked: release it (idempotent,
                    # through the shared boundary, fix 8, so a release fault
                    # never displaces the cancellation), then decide ownership
                    # below exactly as on a timeout. EVERY cleanup that runs
                    # after this capture routes through the shared boundary
                    # with the captured exception pending, and every later
                    # path re-raises the captured object itself (fix 9, QA30
                    # codex BLOCKER 1: the abandonment decision ran outside
                    # the boundary, so an abandonment fault displaced the
                    # captured cancellation).
                    _cleanup_boundary(
                        exc if isinstance(exc, _PENDING_CANCELLATIONS)
                        else None,
                        release_launcher, "parked launcher release")
                    interrupted = exc
                if not launched and _cleanup_boundary(
                        interrupted, abandon_unfinished,
                        "unfinished-launch abandonment"):
                    abandoned = True
            if abandoned:
                # The refusal is constructed through the boundary too: a
                # raising constructor while the captured cancellation is
                # pending must never displace it (best-effort: such a fault
                # costs the refusal detail, never the cancellation).
                refusal = _cleanup_boundary(
                    interrupted, make_refusal, "abandonment refusal")
                if interrupted is not None:
                    raise interrupted from refusal
                raise refusal
            self._launcher = None
            if interrupted is not None:
                # The fork result IS recorded: this close() stays the owner.
                # Finish the bounded collection first -- through the shared
                # boundary (fix 9, QA30 codex BLOCKER 1), so a collection
                # failure never displaces a captured cancellation -- then
                # re-raise the captured object, with any collection failure
                # chained beneath it (never replacing it). A captured
                # NON-cancellation stays reachable as the collection
                # failure's context; the outward-priority guarantee itself
                # is scoped to cancellations (PD-335), and a cancellation
                # BORN in the collection now wins over a captured
                # non-cancellation, per the fix-7 premise.
                try:
                    raise interrupted
                finally:
                    _cleanup_boundary(interrupted, finish_close,
                                      "owner collection finish")
        self._finish_close()

    def _interrupt_collect(self):
        """Last-resort owner for a cancellation landing INSIDE the collection
        itself (the receipt read, the bounded reap wait, or the exit proof):
        read the receipt if it is still pending, escalate NOW through the
        licensed signals only (guardian freeze, the receipt-identified
        subject's SIGKILL through its held pidfd, guardian SIGKILL), and
        bounded-reap the guardian, so the re-raised cancellation never strands
        a live, owner-less guardian or subject. The subject's descendants
        hold no forking-parent handoff: the escalation names them and leaves
        them, NOT signalled (D-385-CURRENT-CHILD), the disclosed orphan-escape
        residual. A guardian already collected can still
        owe its subject cleanup (`collected` and `cleaned` are SEPARATE
        states, QA20 codex F1): the subject-only receipt kill runs here too
        (the collected guardian is dead: no census, fix 2y, D2). The
        cancellation is what propagates; a second cancellation landing here is
        outside the guarantee."""
        import os
        import time
        if self.pid is None:
            return
        if self.collected:
            if not self.cleaned:
                self._address_failed_subject()
            return
        self._recv_subject()
        self._escalate()
        deadline = time.monotonic() + _FIXTURE_CLEANUP_GRACE
        while time.monotonic() < deadline:
            try:
                waited, _raw = os.waitpid(self.pid, os.WNOHANG)
            except OSError as exc:
                # Narrowed (fix 8): deadline/cancellation semantics
                # propagate; only a genuine reap failure ends this
                # bounded wait.
                if isinstance(exc, (TimeoutError, InterruptedError)):
                    raise
                return
            if waited == self.pid:
                self.collected = True
                return
            time.sleep(0.005)

    def _escalation_refusal(self, failure):
        """Annotate an escalated refusal with ONLY what the escalation
        actually did (QA19 F3), accounting every observed member (fix 2y,
        codex F2; a member no census observed -- the disclosed fork-race
        and pid-wraparound residual -- can be neither addressed nor
        accounted, per the ruling PD-335-TREE-CLAIM-STALL):
        "subject tree killed" is claimed ONLY when the guardian-anchored
        census addressed every OBSERVED member (a census can only address
        members it observed) AND the bounded post-kill verification
        census then OBSERVED no live, signalable member left in two
        consecutive clean passes (fix 2z: the claim rests on observation,
        never on the kill sends alone; the maintainer ruling
        PD-335-TREE-CLAIM-STALL words even that claim as an observation with
        its residual, never a proof); a census with skipped or unknown
        members names them, and unverifiable entries -- possible members,
        never established ones (round 24, claude F1) -- are named apart; a
        subject-only kill (no guardian ownership) disclosed its unaddressed
        descendants (the orphan-escape residual, pdeathsig-backed, D2). A
        census kill recorded by a PRIOR interrupted close ("tree"/"partial")
        is disclosed with the same accounting, claiming the pidfd exit proof
        only when this close actually ran it (fix 2z, claude F4). The freeze
        needs the guardian's pidfd, and the subject kill with its exit proof
        needs the receipt's subject pidfd, so a pid-only receipt
        (pidfd-absent host) is a guardian-only escalation whose subject
        cleanup is UNVERIFIED, and a missing receipt is INCOMPLETE cleanup
        -- never a claimed kill this close() could not address (QA18 codex
        F2)."""
        frozen = ("guardian frozen" if self.pidfd is not None
                  else "guardian not frozen: no pidfd")
        if self._subject_kill == "tree":
            # The ONLY state that claims a killed tree: the guardian-anchored
            # census addressed EVERY observed member and the bounded
            # verification census then OBSERVED no live member in two
            # consecutive clean passes (fix 2y, fix 2z, maintainer ruling
            # PD-335-TREE-CLAIM-STALL) -- and the claim is worded as that
            # observation with its residual, never as proof.
            raise ChildStatusUnavailable(
                str(failure) + "; after bounded-close escalation ({}, subject "
                "tree killed: every observed member addressed, none observed "
                "in two consecutive clean censuses -- an observation, not a "
                "proof: a continuously forking chain or pid wraparound can "
                "evade it -- guardian SIGKILL)".format(frozen)) from failure
        if self._subject_kill == "partial":
            if self._subject_skipped is None:
                accounting = "members unknown (census unreadable)"
            else:
                members, unverifiable = self._subject_skipped
                parts = []
                if members:
                    parts.append("members {} unaddressed".format(sorted(members)))
                if unverifiable:
                    # Never "members": their membership was never established
                    # (round 24, claude F1).
                    parts.append("entries unverifiable (possible members): "
                                 "{}".format(sorted(unverifiable)))
                accounting = ", ".join(parts) if parts else "members unknown"
            proof = ("subject exit proven on its pidfd"
                     if self.subject_pidfd is not None
                     else "subject SIGKILL attempted through its held pidfd, "
                     "exit not re-proven by this close")
            raise ChildStatusUnavailable(
                str(failure) + "; after bounded-close escalation ({}, {}, "
                "group census incomplete: {}, guardian "
                "SIGKILL)".format(frozen, proof, accounting)) from failure
        if self.subject_pidfd is not None:
            raise ChildStatusUnavailable(
                str(failure) + "; after bounded-close escalation ({}, subject "
                "exit proven on its pidfd, group kill skipped: no guardian "
                "ownership, descendants, if any, unaddressed "
                "(orphan-escape residual, pdeathsig-backed), guardian "
                "SIGKILL)".format(frozen)) from failure
        if self.subject_pid is not None:
            raise ChildStatusUnavailable(
                str(failure) + "; after bounded-close guardian-only escalation "
                "WITHOUT a subject pidfd ({}, guardian SIGKILL): subject "
                "cleanup unverified".format(frozen)) from failure
        raise ChildStatusUnavailable(
            str(failure) + "; after bounded-close escalation WITHOUT a subject "
            "receipt ({}, guardian SIGKILL): subject cleanup "
            "incomplete".format(frozen)) from failure

    def _unverified_refusal(self, failure):
        """A pid-only receipt (pidfd-absent host) leaves a failed guardian's
        subject cleanup UNVERIFIABLE on the un-escalated path too (QA20 claude
        F3): no pidfd can prove the subject's exit and no pin can license a
        group kill, so the re-raise names the degradation instead of restating
        the bare failure. The subject-side protections (ack EOF, pdeathsig)
        remain the documented residual coverage."""
        raise ChildStatusUnavailable(
            str(failure) + "; guardian failure WITHOUT a subject pidfd "
            "(group kill skipped: not ownership-pinned): subject cleanup "
            "unverified") from failure

    def _ownership_lost_refusal(self):
        """Build the LOST-OWNERSHIP refusal (fix 2y, claude Finding 2): the
        guardian disappeared without this close()'s reap (a prior collection
        was interrupted mid-record, or a foreign reaper ran), so the guardian
        is DEAD and its status is gone. The held receipt still identifies the
        subject: kill it SUBJECT-ONLY through its already-held pidfd and
        prove its disappearance the same way, or refuse naming exactly what
        could not run. Descendants -- in ANY group: no census can run
        without an owning guardian -- are the documented orphan-escape
        residual (pdeathsig-backed, D2), disclosed, never claimed killed.
        Returns the refusal for the caller to raise from the reap's
        ChildProcessError."""
        import select
        if self.subject_pidfd is None:
            if self.subject_pid is not None:
                return ChildStatusUnavailable(
                    "guardian ownership lost; no subject pidfd held: subject "
                    "cleanup unverified")
            return ChildStatusUnavailable(
                "guardian ownership lost; no subject receipt: subject "
                "cleanup incomplete")
        self._address_failed_subject()
        poller = select.poll()
        poller.register(self.subject_pidfd, select.POLLIN)
        if not poller.poll(int(_FIXTURE_CLEANUP_GRACE * 1000)):
            return ChildStatusUnavailable(
                "guardian ownership lost; subject SIGKILL attempted through "
                "its held pidfd, exit not confirmed: pid {} may "
                "survive".format(self.subject_pid))
        self.cleaned = True
        return ChildStatusUnavailable(
            "guardian ownership lost; receipt subject killed on its held "
            "pidfd and exit proven (subject-only: no guardian ownership); "
            "descendants, if any, unaddressed (orphan-escape "
            "residual, pdeathsig-backed)")

    def _finish_close(self):
        import os
        import select
        import signal
        import time
        pending = None

        # Ownership first (#378 P1): the attribute lets go of the number
        # before os.close, so a raising close (the number counts as
        # released) never leaves it named for a later signal through it.
        def close_guardian_pidfd():
            fd, self.pidfd = self.pidfd, None
            if fd is not None:
                os.close(fd)

        def close_subject_pidfd():
            fd, self.subject_pidfd = self.subject_pidfd, None
            if fd is not None:
                os.close(fd)

        def close_report():
            self.report.close()

        def close_tail():
            # Subject pidfd, then the report channel: a failure in the
            # subject close never skips the report close, and a
            # cancellation BORN in the subject close is pending for it
            # (the fix 7 rule, extended to this sequence by fix 8).
            try:
                close_subject_pidfd()
            except BaseException as tail_exc:
                _cleanup_boundary(
                    tail_exc if isinstance(tail_exc,
                                           _PENDING_CANCELLATIONS)
                    else pending,
                    close_report, "report channel close")
                raise
            _cleanup_boundary(pending, close_report,
                              "report channel close")

        def close_owned_handles():
            # Guardian pidfd first, then the tail: every owned handle is
            # disposed even when an earlier close fails.
            try:
                close_guardian_pidfd()
            except BaseException as head_exc:
                _cleanup_boundary(
                    head_exc if isinstance(head_exc,
                                           _PENDING_CANCELLATIONS)
                    else pending,
                    close_tail, "subject pidfd and report close")
                raise
            _cleanup_boundary(pending, close_tail,
                              "subject pidfd and report close")

        def interrupt_collect():
            self._interrupt_collect()

        try:
            escalated = False
            try:
                # Read the buffered subject receipt BEFORE closing the control socket;
                # closing it is what requests tree cleanup (EOF), and cancellation
                # addresses this guardian and its receipt, never a stale PID.
                self._recv_subject()
                self.control.close()
                self.peer.close()
                if self.pid is not None and not self.collected:
                    if not self.armed:
                        _fixture_signal(self.pid, signal.SIGKILL, self.pidfd,
                                        forked=True)
                    # A cancelled call no longer needs the subject's execution time:
                    # wait within a bounded cleanup budget (twice the grace, so an
                    # honest guardian's own grace-bounded drain fits), then escalate
                    # rather than blocking for the remaining execution budget. An
                    # escalated collection is recorded on the cannot-evaluate channel
                    # below; it is never read as success.
                    deadline = time.monotonic() + 2 * _FIXTURE_CLEANUP_GRACE
                    while True:
                        try:
                            waited, raw = os.waitpid(self.pid, os.WNOHANG)
                        except ChildProcessError as exc:
                            self.collected = True
                            raise self._ownership_lost_refusal() from exc
                        if waited != 0:
                            break
                        if time.monotonic() >= deadline:
                            if escalated:
                                raise ChildStatusUnavailable(
                                    "guardian cleanup deadline: not collected after escalation")
                            escalated = True
                            self._escalate()
                            deadline = time.monotonic() + _FIXTURE_CLEANUP_GRACE
                        time.sleep(0.005)
                    self.collected = True
                    if waited != self.pid:
                        raise ChildStatusUnavailable("unexpected guardian cleanup PID")
                    if self.armed:
                        self.unresolved = True
                        try:
                            self._read_report(raw)
                        except ChildStatusUnavailable:
                            pass  # recorded by _read_report; addressed below
                # HOISTED out of the not-collected gate (QA19 F1): the subject
                # kill and exit proof run on ANY guardian failure, including one
                # FIRST collected by poll() -- the layer's primary collection
                # path, which killed the subject pin-first at collection time
                # and recorded the failure for this close(). An armed guardian
                # collected WITHOUT a validated status or recorded failure is
                # UNRESOLVED (QA20 codex F1): the subject's fate is unknown, so
                # close() treats it as a failure of its own. A startup failure
                # start() already surfaced (never armed) has no subject and
                # nothing to re-raise.
                failure = self._failure if self.armed else None
                if failure is None and self.unresolved:
                    failure = self._record_failure(ChildStatusUnavailable(
                        "fixture guardian collected without validated status: "
                        "supervision unresolved"))
                if (escalated or failure is not None) and self.subject_pidfd is not None:
                    if failure is not None and not escalated:
                        self._address_failed_subject()
                    # A pidfd polls readable on exit even for a non-child (init
                    # reaps the orphan): prove the subject disappeared, or refuse
                    # loudly, never silence.
                    poller = select.poll()
                    poller.register(self.subject_pidfd, select.POLLIN)
                    if not poller.poll(int(_FIXTURE_CLEANUP_GRACE * 1000)):
                        raise ChildStatusUnavailable(
                            ("escalation" if escalated else "guardian failure")
                            + " could not confirm subject exit: pid {} may "
                            "survive".format(self.subject_pid))
                self.cleaned = True
                if failure is not None:
                    if not escalated:
                        if (self.subject_pidfd is None and self.subject_pid is not None
                                and self._subject_kill is None):
                            self._unverified_refusal(failure)
                        if self._subject_kill == "subject-only":
                            # The un-escalated failure paths run with a DEAD
                            # guardian, so the receipt kill was SUBJECT-ONLY
                            # (fix 2y, D2): disclose the residual, never a
                            # killed tree.
                            raise ChildStatusUnavailable(
                                str(failure) + "; subject exit proven on its "
                                "pidfd (subject-only kill through the held "
                                "receipt pidfd: the guardian is dead, no "
                                "ownership licenses a member census); "
                                "descendants, if any, unaddressed "
                                "(orphan-escape residual, "
                                "pdeathsig-backed)") from failure
                        if self._subject_kill is not None:
                            # A PRIOR interrupted close()'s ESCALATION already
                            # ran the guardian-anchored census kill
                            # ("tree"/"partial"): disclose THAT accounting,
                            # never a subject-only proof this close did not
                            # run (fix 2z, claude F4).
                            self._escalation_refusal(failure)
                        raise failure
                    self._escalation_refusal(failure)
            except ChildStatusUnavailable:
                raise
            except BaseException as exc:
                # The interrupt owner is module-owned cleanup too
                # (fix 8): routed through the boundary, a collection
                # failure can never displace the cancellation it serves.
                _cleanup_boundary(
                    exc if isinstance(exc, _PENDING_CANCELLATIONS)
                    else None,
                    interrupt_collect, "interrupt-owner collection")
                raise
        except BaseException as exc:
            # Capture the exception already propagating into the
            # descriptor close below (fix 8, QA29 gemini BLOCKER): these
            # closes ran in a bare finally OUTSIDE any boundary, so an
            # os.close failure could displace a cancellation raised
            # anywhere in this collection.
            pending = exc
            raise
        finally:
            _cleanup_boundary(pending, close_owned_handles,
                              "held descriptor and report close")


def _run_fixture_process(argv, *, timeout=120, cwd=None, env=None):
    """Capture output and raw subject status through the shared tree owner."""
    import math
    import os
    import signal
    import subprocess
    import tempfile
    import time
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise ChildStatusUnavailable("unowned SIGCHLD disposition")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("fixture timeout must be finite and positive")
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("fixture timeout must be finite and positive")
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        def exec_subject():
            # Subject process only (start() is parent-only): rewire stdio onto the
            # kept capture files, then the argv program replaces this image.
            # setsid and PR_SET_PDEATHSIG already happened in the subject wrapper.
            try:
                os.dup2(out.fileno(), 1)
                os.dup2(err.fileno(), 2)
                if cwd is not None:
                    os.chdir(cwd)
                os.execvpe(argv[0], argv, os.environ if env is None else env)
            except BaseException:
                import traceback
                traceback.print_exc()
                os._exit(127)

        child = _FixtureProcess(time.monotonic() + timeout,
                                keep_fds=(out.fileno(), err.fileno()),
                                subject=exec_subject)
        try:
            child.start()
            while True:
                status = child.poll()
                # Deadline expiry is authoritative BEFORE accepting completion:
                # the clock is sampled AFTER the poll, so only a pre-expiry
                # sample proves collection preceded the deadline. A completion
                # first observed after expiry is a timeout, whatever order the
                # guardian and this collector were scheduled in.
                overdue = time.monotonic() >= child.deadline
                if status is None:
                    if overdue:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    time.sleep(0.005)
                    continue
                if child.timed_out or overdue:
                    raise subprocess.TimeoutExpired(argv, timeout)
                break
        except TimeoutError as exc:
            raise subprocess.TimeoutExpired(argv, timeout) from exc
        finally:
            child.close()
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(argv, os.waitstatus_to_exitcode(child.status),
                                           out.read(), err.read())


def _fixture_main(nonce, fixture_id, argv, expected, process_fixture):
    """Emit only after the requested fixture and its expected-outcome assertion finish."""
    import json
    import sys
    if process_fixture:
        # Crash subjects and Git setup cannot emit Python completion records.
        # Their supervisor asserts the raw outcome before attesting completion.
        result = _run_fixture_process(argv)
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
        rc = result.returncode
    else:
        if argv[:3] != [sys.executable, "-I", "-B"] or len(argv) < 4:
            raise ValueError("fixture requires an explicit isolated Python command")
        if argv[3] != "-c" or len(argv) < 5:
            raise ValueError("assertion fixtures require a returning Python body")
        sys.argv = ["-c", *argv[5:]]
        namespace = {"__name__": "__opf_fixture__"}
        body = "def _opf_fixture():\n" + "".join(
            "    " + line + "\n" for line in argv[4].splitlines())
        exec(compile(body, "<opf-fixture>", "exec"), namespace)
        rc = namespace["_opf_fixture"]()  # SystemExit (even zero) is a failure
    if type(rc) is not int or rc != expected:
        raise AssertionError("fixture {!r}: expected exit {}, observed {!r}".format(
            fixture_id, expected, rc))
    sys.stdout.flush()
    sys.stdout.buffer.write(("\nOPF-FIXTURE " + json.dumps([nonce, fixture_id]) + "\n").encode())
    sys.stdout.buffer.flush()


def run_status_owned(argv, *, fixture_id, expected_returncode=0, process_fixture=False,
                     timeout=120, cwd=None, env=None, capture_output=False, text=False,
                     stdout=None, stderr=None, check=False):
    """Success requires a fresh (nonce, fixture) completion record AND observed exit zero.

    Assertion bodies must return an integer; SystemExit never attests completion.
    process_fixture=True is the explicit raw CLI/crash path.
    expected_returncode describes the subject, not the supervisor: an expected CLI
    refusal or deliberate crash is asserted inside the fixture, whose own exit must be
    zero. Returned stdout excludes the protocol trailer. Missing/mismatched records are
    failures; unavailable status is a distinct cannot-evaluate even with a valid record.
    Records bind trusted fixtures to launches; they do not authenticate malicious code
    that can read its argv and deliberately forge the record.
    """
    import json
    import secrets
    import subprocess
    import sys
    from pathlib import Path
    if not isinstance(fixture_id, str) or not fixture_id:
        raise ValueError("fixture_id must be nonempty")
    if type(expected_returncode) is not int:
        raise ValueError("expected_returncode must be an integer")
    if stdout not in (None, subprocess.PIPE) or stderr not in (None, subprocess.PIPE):
        raise ValueError("fixture output must be captured")
    nonce = secrets.token_hex(32)
    code = ("import sys, json; sys.path.insert(0, " + repr(str(Path(__file__).resolve().parent))
            + "); from _opf_emit import _fixture_main; "
            + "_fixture_main(*json.loads(sys.argv[1]))")
    command = [sys.executable, "-I", "-B", "-c", code,
               json.dumps([nonce, fixture_id, list(argv), expected_returncode, process_fixture])]
    result = _run_fixture_process(command, timeout=timeout, cwd=cwd, env=env)
    trailer = ("\nOPF-FIXTURE " + json.dumps([nonce, fixture_id]) + "\n").encode()
    if not result.stdout.endswith(trailer):
        raise FixtureIncomplete("missing/mismatched completion for " + fixture_id
                                + ": " + result.stderr.decode("utf-8", "replace"))
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    result.stdout = result.stdout[:-len(trailer)]
    if text:
        result.stdout = result.stdout.decode("utf-8")
        result.stderr = result.stderr.decode("utf-8")
    return result


def _fixture_child_reaped(pid):
    """Assert collection without signalling; the owning tree helper handles cleanup."""
    import os
    try:
        waited, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return True
    except OSError as exc:
        raise ChildStatusUnavailable("cannot evaluate fixture reap: " + str(exc)) from exc
    if waited not in (0, pid):
        raise ChildStatusUnavailable("unexpected cleanup fixture PID")
    return False


def _bounded_setup_error(exc):
    """Retain the error type and bounded guardian receipt on the sentinel channel."""
    return "SETUP-ERROR:" + type(exc).__name__ + ":" + str(exc)[:8192]


def run_bounded(thunk, timeout_s=30, mem_bytes=1024 * 1024 * 1024, keep_fds=()):
    """Run `thunk` (a zero-arg callable returning a short str) in a bounded CHILD PROCESS and return that
    str, or a sentinel: 'TIMEOUT' (wall-clock bound tripped), 'OOM' (address-space bound tripped),
    'CHILD-DIED', 'ERROR:<Type>' (the thunk raised), or 'SETUP-ERROR:<Type>[:detail]' (the child could NOT install
    its bounds, fork failed, or child ownership is unprovable: a cannot-evaluate, never a normal result). Several
    adversarial vectors drive an engine over a declared 10**9 high-water/span or a deeply-shared DAG; run
    IN-PROCESS a regression that reverted the bounded counting or an identity short-circuit would HANG or
    OOM the whole self-test before it could report. This watchdog turns such a regression into a
    deterministic sentinel the assertion catches, without weakening the assertion (a correct engine returns
    its real verdict token well inside the bounds). A SETUP-ERROR sentinel is never equal to any expected
    verdict token, so a check whose bounds could not be installed FAILS closed rather than reading a
    possibly-unbounded run as a clean pass (no-concealed-failure).

    Test-harness only: OPF self-tests, including the FIFO refusal probes, share this
    implementation. Timer setup happens only in the child; the parent's timers,
    handlers, masks and pending signals are never borrowed. Production validators fork nothing.

    Descriptor contract (fd allowlist): the thunk sees ONLY stdio, this call's result pipe and the
    caller-declared keep_fds -- the guardian closes every other inherited descriptor before the thunk
    can run. A thunk relying on an undeclared caller descriptor fails loudly and deterministically
    (typically ERROR:OSError from EBADF), never a sometimes-working leak; keep_fds is the sanctioned
    path for a pre-opened descriptor the thunk needs (see _FixtureProcess). A guardian that cannot
    obtain an authoritative /proc/self/fd census REFUSES startup (a SETUP-ERROR sentinel), never
    sweeps partially. Lock contract: the fork runs on a private launcher thread, so the thunk must
    not depend on reacquiring locks (including cold-import module locks) held by caller threads at
    call time; only the launcher thread survives into the child, and such a thunk deadlocks there
    and is collected as a loud TIMEOUT (guardian dependencies themselves are preloaded pre-fork).

    Hardening: (fork-less) a host without os.fork returns SETUP-ERROR WITHOUT running the thunk, never the
    thunk's own result run unbounded. (child) the child resets SIGALRM to SIG_DFL AND UNBLOCKS it in its
    signal mask, so neither an inherited SIG_IGN disposition nor an inherited BLOCKED mask can defeat the
    child timer; it then installs BOTH bounds or writes SETUP-ERROR without running the thunk.
    Independently, a parent-owned monotonic deadline starts BEFORE fork and covers child startup,
    nonblocking pipe collection and exit. On expiry the parent kills the child's process group and
    reaps the child, returning TIMEOUT even if bytes were buffered. Exceptions also kill and reap an owned child;
    neither path relies on the child reaching its timer setup. Both pipe fds close on fork failure.

    Tree ownership, cancellation and portability limits are those of _FixtureProcess.
    A guardian owns every descendant; the caller never signals a captured subject PID.
    """
    import os
    import signal
    import select
    import time
    import math
    if not hasattr(os, "fork"):
        # A bound could NOT be installed on a fork-less host: a cannot-evaluate. Return the SETUP-ERROR
        # sentinel WITHOUT invoking the thunk (never run it unbounded); the caller fails closed because the
        # sentinel is never equal to an expected verdict token.
        return "SETUP-ERROR:NoFork"
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        return "SETUP-ERROR:ChildOwnership"
    if not all(hasattr(os, name) for name in ("waitid", "P_PID", "WNOWAIT")):
        return "SETUP-ERROR:NoWaitid"
    import resource
    # Reject an UNBOUNDED or INVALID control BEFORE forking/running the thunk (codex round-6): a
    # timeout_s <= 0 installs setitimer(0, 0) which DISARMS the timer (no wall-clock bound at all), and a
    # mem_bytes of RLIM_INFINITY (or <= 0) installs no usable address-space cap, yet the child would still
    # run the thunk unbounded and its successful no-op setrlimit/setitimer would read as "bounds installed".
    # A control that cannot bound the child is a cannot-evaluate, so return the SETUP-ERROR sentinel here
    # rather than let the thunk run unbounded (guard-input-soundness; no-concealed-failure; a bool is not a
    # valid numeric control). A NaN or an infinity cannot arm a finite itimer either (setitimer raises on it
    # in the child), so reject them PRE-FORK too for symmetry: never fork a child that could only fail setup
    # (NaN is !=-itself; +inf is caught explicitly, -inf by the <= 0 test).
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or timeout_s != timeout_s or timeout_s == float("inf") or timeout_s <= 0):
        return "SETUP-ERROR:BadTimeout"
    # Conversion and deadline arithmetic must precede pipe allocation, including huge integers.
    try:
        timeout_s = float(timeout_s)
        deadline = time.monotonic() + timeout_s
    except (OverflowError, ValueError):
        return "SETUP-ERROR:BadTimeout"
    if not math.isfinite(timeout_s) or not math.isfinite(deadline):
        return "SETUP-ERROR:BadTimeout"
    # RLIM_INFINITY is the "no cap" sentinel; its integer representation is platform-dependent (it is -1
    # on Linux, a large positive on others), so reject it by identity AND by the <= 0 / >= positive-sentinel
    # bounds, rather than assuming one sign. Either way an unbounded or non-positive address-space control
    # is a cannot-evaluate, never a silently-uncapped child.
    _rlim_inf = resource.RLIM_INFINITY
    if (isinstance(mem_bytes, bool) or not isinstance(mem_bytes, int)
            or mem_bytes <= 0 or mem_bytes == _rlim_inf
            or (_rlim_inf > 0 and mem_bytes >= _rlim_inf)):
        return "SETUP-ERROR:BadMemBound"
    try:
        rfd, wfd = os.pipe()
    except OSError as exc:
        return _bounded_setup_error(exc)
    def bounded_subject():                               # subject: bounded, writes one short token, never returns
        os.close(rfd)
        try:
            # Keep the child timer as an independent bound, with a deliverable disposition and mask.
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            if hasattr(signal, "pthread_sigmask"):
                signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM})
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            signal.setitimer(signal.ITIMER_REAL, timeout_s)
        except BaseException as exc:                     # noqa: BLE001 (bounds NOT installed: never run unbounded)
            try:
                os.write(wfd, ("SETUP-ERROR:" + type(exc).__name__).encode("utf-8", "replace")[:200])
            except OSError:
                pass
            os._exit(0)
        try:
            payload = str(thunk()).encode("utf-8", "replace")[:200]
        except MemoryError:
            payload = b"OOM"
        except BaseException as exc:                     # noqa: BLE001 (child boundary: any failure -> token)
            payload = ("ERROR:" + type(exc).__name__).encode("utf-8", "replace")[:200]
        try:
            os.write(wfd, payload)
        except OSError:
            pass
        os._exit(0)

    child = None
    try:
        child = _FixtureProcess(deadline, keep_fds=(rfd, wfd, *keep_fds),
                                subject=bounded_subject)
        child.start()
    except BaseException as exc:
        try:
            if child is not None:
                child.close()
        finally:
            try:
                os.close(rfd)
            finally:
                os.close(wfd)
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, TimeoutError):
            return "TIMEOUT"
        return _bounded_setup_error(exc)
    # parent: never borrow a timer, handler, signal mask or pending notification.
    data = b""
    wstatus = None
    timed_out = False
    failures = []
    try:
        os.close(wfd)
        os.set_blocking(rfd, False)
        poller = select.poll()
        poller.register(rfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        eof = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            # EOF depends on every writer: a concurrent helper fork can inherit our
            # writer before the caller closes it. Only this call's guardian
            # receipt certifies that its tree has stopped writing; never wait
            # for a sibling's inherited descriptor to close before polling it.
            wstatus = child.poll()
            if wstatus is not None:
                # Deadline expiry is authoritative over the collected receipt:
                # the clock is sampled after the poll (see _run_fixture_process).
                timed_out = child.timed_out or time.monotonic() >= deadline
                if not eof:
                    # The receipt follows ECHILD, so the complete (at most 200
                    # byte) payload is already buffered. Drain without waiting
                    # for EOF, including when status wins the first poll.
                    try:
                        data = (data + os.read(rfd, 200))[:200]
                    except BlockingIOError:
                        pass
                break
            ready = poller.poll(max(1, math.ceil(min(remaining, 0.01) * 1000)))
            if ready:
                try:
                    chunk = os.read(rfd, 200)
                except BlockingIOError:
                    continue
                if not chunk:
                    eof = True
                    poller.unregister(rfd)
                else:
                    data = (data + chunk)[:200]
    except ChildStatusUnavailable as exc:
        failures.append(_bounded_setup_error(exc))
    finally:
        try:
            os.close(rfd)
        finally:
            try:
                child.close()
            except ChildStatusUnavailable as exc:
                # close() re-raises a recorded failure the poll above already
                # reported (QA19 F1) AFTER addressing the receipt-identified
                # subject, possibly ANNOTATED (subject-only kill disclosure,
                # pid-only receipt): report each refusal once, keeping the
                # annotated form when one extends the other.
                sentinel = _bounded_setup_error(exc)
                for index, previous in enumerate(failures):
                    if sentinel.startswith(previous):
                        failures[index] = sentinel
                        break
                    if previous.startswith(sentinel):
                        break
                else:
                    failures.append(sentinel)
    if failures:
        return "; ".join(failures)
    wstatus = child.status
    return "TIMEOUT" if timed_out else _bounded_child_result(data, wstatus)


def _bounded_child_result(data, wstatus):
    """Decide run_bounded's result from the child's pipe bytes AND its TERMINATION STATUS, inspecting the
    status FIRST regardless of any bytes already buffered (codex round-6): a child can write its token and
    THEN be killed by the SIGALRM timer (or any signal), so a complete or partial token in the pipe is NOT
    proof of a clean result. A signal death is the watchdog firing and is therefore the sentinel (SIGALRM
    -> TIMEOUT, any other signal -> CHILD-DIED), never the buffered token read as success; only a child
    that exited NORMALLY with bytes returns those bytes (no-concealed-failure). Kept as a pure module-level
    function so the status-precedence is exercised directly with a real signaled wait-status."""
    import os
    import signal
    if os.WIFSIGNALED(wstatus):
        if os.WTERMSIG(wstatus) == signal.SIGALRM:
            return "TIMEOUT"
        return "CHILD-DIED"
    # A run_bounded child ALWAYS os._exit(0) after writing its token (a real result, ERROR:, SETUP-ERROR:,
    # or OOM), so a NONZERO normal exit is an abnormal death and its buffered bytes are NOT proof of a clean
    # result. Require a SUCCESSFUL exit (WIFEXITED + status 0) before the payload may be returned; anything
    # else is CHILD-DIED. Pre-fix a child that wrote its token then exited nonzero (exit 7) still returned the
    # buffered token (no-concealed-failure; codex round-8 finding 6). Signal death is handled above.
    if not (os.WIFEXITED(wstatus) and os.WEXITSTATUS(wstatus) == 0):
        return "CHILD-DIED"
    if not data:
        return "CHILD-DIED"
    return data.decode("utf-8", "replace")


def _st_guardian_setup_failure(drive, setup_failed, recorded, journal, open_fd=None, private=None, reuse=None):
    """#378: the guardian close vector with its first send raising. Its result must be this vector's named
    red (NOFIRE, the guardian never ran, and WRONG naming SetupFailed) with no LEAK; every resource the setup
    created must be closed through its own object; and descriptors opened afterwards, which take the
    numbers those resources released, must still be the same files after gc.collect(), so no surviving
    object closes a reused number later. Each of those descriptors opens a file this check creates in a
    private directory (`private`, a fresh temporary directory unless a leg passes one it keeps until it has
    checked the numbers), so its (st_dev, st_ino), recorded at open, is unique to this check while the
    directory exists and no other lane's open of a reused number (of /dev/null or any other file) compares
    equal. Each is owned from its open on (a failing fstat of it closes it, its number never yet released)
    and by an ExitStack from its identity on, so an open that fails (`open_fd`, os.open unless a leg injects
    one) releases the ones already opened and is this check's own named failure, never an escaping
    exception. The stack's release closes a number only while fstat still returns the identity recorded at
    its open, so a number this check expected released, and another lane reopened onto any other file, is
    never closed by this check's cleanup either (#378 P1). `reuse`, when a leg passes one, is called with
    the descriptors after gc.collect() and before they are checked, so the leg can put another file on one.
    Returns the failures."""
    import contextlib
    import errno
    import gc
    import os
    import tempfile
    opener = os.open if open_fd is None else open_fd
    created = []

    def failing_send(sock):
        raise OSError(errno.EPIPE, "self-test injected setup failure")
    got = journal._st_close_run(drive(_FixtureProcess._guardian, send=failing_send, created=created), True,
                                recorded, False)
    failures = []
    tags = [problem.split(":")[0] for problem in got]
    if tags != ["NOFIRE", "WRONG"] or setup_failed.__name__ not in got[1] or "injected setup failure" not in got[1]:
        failures.append("guardian-close-reuse setup failure: expected red by NOFIRE and WRONG naming {}, got "
                        "{}".format(setup_failed.__name__, got or "green"))
    if len(created) != 3 or not all(getattr(r, "closed", None) is True or getattr(r, "_closed", None) is True
                                    for r in created):
        failures.append("guardian-close-reuse setup failure: expected its 3 resources each closed through "
                        "its object, got {}".format(created))

    def release(fd, ident):
        """Close fd only while it still names the file recorded at its open, this check's own."""
        try:
            st = os.fstat(fd)
        except OSError:
            return                                        # released already: never closed again
        if (st.st_dev, st.st_ino) != ident:
            return                                        # another file holds the number now: not ours
        try:
            os.close(fd)
        except OSError:
            pass                                          # released either way (close(2)); never re-touched
    with contextlib.ExitStack() as owned:
        if private is None:                               # removed after every descriptor is released
            private = owned.enter_context(tempfile.TemporaryDirectory(prefix="opf-emit-setup-"))
        fresh = []
        ident = []
        try:
            for index in range(4):
                fd = opener(os.path.join(private, "fresh-{}".format(index)), os.O_RDONLY | os.O_CREAT | os.O_EXCL,
                            0o600)
                try:
                    st = os.fstat(fd)
                except BaseException:
                    os.close(fd)                          # never released yet: still this check's own number
                    raise
                owned.callback(release, fd, (st.st_dev, st.st_ino))   # owned from its identity on
                fresh.append(fd)
                ident.append((st.st_dev, st.st_ino))      # this check's own file, recorded at open
        except OSError as exc:
            failures.append("guardian-close-reuse setup failure: opening descriptor {} of 4 after the leg "
                            "failed: {!r}".format(len(fresh) + 1, exc))
            return failures
        del created[:]
        gc.collect()
        if reuse is not None:
            reuse(list(fresh))
        after = []
        for fd in fresh:
            try:
                after.append((os.fstat(fd).st_dev, os.fstat(fd).st_ino))
            except OSError:
                after.append(None)
        if after != ident:
            failures.append("guardian-close-reuse setup failure: a descriptor opened afterwards was closed or "
                            "replaced after gc.collect() ({} became {})".format(ident, after))
    return failures


def _st_guardian_close_reuse():
    """#378 P1: _FixtureProcess._guardian takes ownership of the subject pidfd before closing it after the
    drain. Driven in-process with every process seam stubbed (no fork, no signal mask, os._exit raising), the
    close fails after releasing its number to a reuser (the shared _journal close harness): the guardian
    records the failure at stage "drain" and exits 125, and its cleanup never closes the number again.
    Green is no problem at all, with and without the PROBE watch; under the close-then-rebind body put back
    (os.close(subject_fd), then subject_fd = None) it is red by REUSE alone, the cleanup closing the reuser.
    Every resource the vector creates (the socket pair, the report file) is owned by an ExitStack the moment
    it exists, so a failing setup step is this vector's own named red (SetupFailed), each resource is closed
    once through its object before the harness reads its descriptor table, and nothing is left for the
    harness to close by number while a traceback keeps the object, whose collection would later close a
    reused number in another lane. A setup-failure leg (the first send raising) checks exactly that, and
    again with the second of the descriptors it opens afterwards failing to open: that is its named failure,
    with the descriptor opened first closed. Every descriptor that leg owns opens a file it creates in a
    private directory, so a number it expected released is closed only while fstat shows that file's
    identity, by the leg and by the setup check's own cleanup alike: an identity leg shows a number another
    lane reopened onto another file is never closed and is no failure, a reuse leg shows the setup check
    reports a number replaced after gc.collect() and its cleanup leaves that number open, and a genuine leak
    is still reported and closed.
    The harness is loaded from its sibling FILE by explicit path, as _load_byte_canon_authority loads its
    authority, so this runs under `python3 -I` too. Returns the failures."""
    import contextlib
    import errno
    import gc
    import importlib.util
    import inspect
    import json
    import os
    import signal
    import socket
    import tempfile
    import textwrap
    import time
    import types
    spec = importlib.util.spec_from_file_location("_opf_emit_close_harness",
                                                  Path(__file__).resolve().parent / "_journal.py")
    _journal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_journal)

    class Exited(Exception):
        def __init__(self, code):
            super().__init__(code)
            self.code = code

    class SetupFailed(Exception):
        """A step of the vector's own setup failed: this vector's red, never a green or another lane's."""

    def drive(guardian, send=lambda sock: sock.sendall(b"G"), created=None):
        def call(fault):
            made = [] if created is None else created     # what the setup made, for the setup-failure leg
            with contextlib.ExitStack() as owned:         # each resource owned the moment it exists
                try:
                    pair = socket.socketpair()
                    for end in pair:
                        owned.callback(end.close)
                    made.extend(pair)
                    peer, other = pair
                    report = tempfile.TemporaryFile()
                    owned.callback(report.close)
                    made.append(report)
                    send(other)
                    child = types.SimpleNamespace(_launch_masked=set(), peer=peer, report=report, keep_fds=(),
                                                  subject=None, deadline=time.monotonic() + 3600)

                    def _exit(code):
                        raise Exited(code)
                    stubs = {"_fixture_close_all_except": lambda keep: None, "_fixture_subreaper": lambda: None,
                             "_fixture_pidfd": lambda pid: fault.arm(os.open(os.devnull, os.O_RDONLY)),
                             "_fixture_send_subject": lambda peer, subject, fd: None,
                             "_fixture_ack_subject": os.close,
                             "_fixture_cleanup_deadline": lambda deadline=None: deadline,
                             "_fixture_drain": lambda subject, fd=None, deadline=None, subject_ref=None: 0}
                    calls = {(os, "setpgid"): lambda pid, pgrp: None, (os, "fork"): lambda: 1 << 22,
                             (os, "waitid"): lambda *args: (), (os, "_exit"): _exit,
                             (signal, "pthread_sigmask"): lambda how, mask: set()}   # the caller's mask untouched
                    seams = guardian.__globals__              # the reverted body runs in its own namespace
                    saved = {name: seams[name] for name in stubs}, {seam: getattr(*seam) for seam in calls}
                    enabled = gc.isenabled()
                except Exception as exc:
                    raise SetupFailed("guardian close vector setup failed: {!r}".format(exc)) from exc
                try:
                    seams.update(stubs)
                    for (module, name), stub in calls.items():
                        setattr(module, name, stub)
                    guardian(child)
                except Exited as exc:
                    report.seek(0)
                    failure = json.loads(report.read().decode("ascii"))
                    if exc.code == 125 and failure["stage"] == "drain" \
                            and failure["error"]["errno"] == fault.err.errno:
                        raise _journal._StSentinel("recorded at drain")
                    raise
                finally:
                    seams.update(saved[0])
                    for (module, name), real in saved[1].items():
                        setattr(module, name, real)
                    if enabled:
                        gc.enable()
        return call

    def recorded(exc):
        return type(exc) is _journal._StSentinel

    failures = []
    for watch in (False, True):
        got = _journal._st_close_run(drive(_FixtureProcess._guardian), True, recorded, watch)
        if got:
            failures.append("guardian-close-reuse (watch={}): expected green, got {}".format(watch, got))
    failures += _st_guardian_setup_failure(drive, SetupFailed, recorded, _journal)
    opened = []

    def second_open_fails(path, flags, mode):
        """The setup-failure leg's descriptor opens with the second one failing (EMFILE); each one opened is
        recorded with its (st_dev, st_ino), taken while this leg still owns the number."""
        if opened:
            raise OSError(errno.EMFILE, "self-test injected open failure")
        fd = os.open(path, flags, mode)
        try:
            st = os.fstat(fd)
        except BaseException:
            os.close(fd)
            raise
        opened.append((fd, (st.st_dev, st.st_ino)))
        return fd

    def close_if_left_open(fd, ident):
        """A number this leg expected released is "left open", and closed here, only while fstat still shows
        ident, a file this leg created in its private directory, which no other lane opens and whose inode
        stays taken while the directory exists. The number may since belong to another thread's open, so a
        number naming any other file (another lane's /dev/null included) is never closed (#378 P1). Returns
        whether it closed fd."""
        try:
            st = os.fstat(fd)
        except OSError:
            return False
        if (st.st_dev, st.st_ino) != ident:
            return False
        os.close(fd)
        return True

    def own_file(name):
        """A descriptor on a new file in the private directory, with its (st_dev, st_ino) taken at open."""
        fd = os.open(os.path.join(private, name), os.O_RDONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            st = os.fstat(fd)
        except BaseException:
            os.close(fd)
            raise
        return fd, (st.st_dev, st.st_ino)

    def identity(fd):
        try:
            st = os.fstat(fd)
        except OSError:
            return None
        return st.st_dev, st.st_ino
    with tempfile.TemporaryDirectory(prefix="opf-emit-setup-") as private:   # kept until every number is checked
        got = _st_guardian_setup_failure(drive, SetupFailed, recorded, _journal, second_open_fails, private)
        if len(got) != 1 or "opening descriptor 2 of 4" not in got[0] or "injected open failure" not in got[0]:
            failures.append("guardian-close-reuse setup failure with its second open failing: expected that "
                            "named failure alone, got {}".format(got or "green"))
        for fd, ident in opened:
            if close_if_left_open(fd, ident):
                failures.append("guardian-close-reuse setup failure with its second open failing: the "
                                "descriptor opened first was left open")
        # Another lane opening a file onto the released number: dup2 puts a descriptor on this leg's own
        # "other-lane" file onto a number this leg still owns, so the number is never free for a real lane to
        # take meanwhile. It must not be closed and must not be reported; the leg then closes it only while
        # it still names the other-lane file, whose identity no other lane's open shares.
        other, other_ident = own_file("other-lane")
        try:
            reused, ident = own_file("reused")
            try:
                os.dup2(other, reused)
            except BaseException:
                os.close(reused)                          # dup2 failed: still this leg's own file
                raise
            if close_if_left_open(reused, ident):
                failures.append("guardian-close-reuse identity check: closed a number another lane reopened "
                                "onto another file")
            elif identity(reused) != other_ident:
                failures.append("guardian-close-reuse identity check: the number another lane reopened no "
                                "longer names that lane's file")
            else:
                os.close(reused)                          # the other-lane file, which this leg owns
            # The setup check's own cleanup: after gc.collect() another lane's file replaces the second
            # descriptor the check opened (dup2 onto a number the check still owns). The check must report
            # it replaced, and its ExitStack must leave the number open, as it names another file.
            hit = []

            def reuse(fresh):
                os.dup2(other, fresh[1])
                hit.append(fresh[1])
            got = _st_guardian_setup_failure(drive, SetupFailed, recorded, _journal, reuse=reuse)
            if len(got) != 1 or "closed or replaced" not in got[0]:
                failures.append("guardian-close-reuse setup failure with a number another lane reused: expected "
                                "that number reported replaced alone, got {}".format(got or "green"))
            if len(hit) != 1 or identity(hit[0]) != other_ident:
                failures.append("guardian-close-reuse setup failure: its cleanup closed a number another lane "
                                "reused ({})".format(hit))
            else:
                os.close(hit[0])                          # the other-lane file, which this leg owns
        finally:
            os.close(other)
        # A genuine leak: a number still naming this leg's own file is reported and closed.
        leaked, ident = own_file("leaked")
        if not close_if_left_open(leaked, ident) or identity(leaked) == ident:
            failures.append("guardian-close-reuse identity check: a descriptor left open on this leg's own "
                            "file was not reported and closed")
            if identity(leaked) == ident:
                os.close(leaked)
    source = textwrap.dedent(inspect.getsource(_FixtureProcess._guardian))
    new = ("fd, subject_fd = subject_fd, None         # ownership first: a failed close is never\n"
           "            os.close(fd)                              # closed again by the cleanup (P1, #378)\n")
    if source.count(new) != 1:
        return failures + ["guardian-close-reuse: revert target found {} times".format(source.count(new))]
    ns = dict(globals())
    exec(compile(source.replace(new, "os.close(subject_fd)\n            subject_fd = None\n"), __file__,
                 "exec"), ns)
    red = _journal._st_close_run(drive(ns["_guardian"]), True, recorded, False)
    if [problem.split(":")[0] for problem in red] != ["REUSE"]:
        failures.append("guardian-close-reuse under the close-then-rebind body: expected red by REUSE alone, "
                        "got {}".format(red or "green"))
    return failures


def self_test():
    """Round-trip fuzz over adversarial bodies, canonical-form determinism, constrained-subset coverage
    (accepted and rejected), and byte-canon cleanliness verified against _byte_canon itself."""
    failures = []

    # _byte_canon is the authority for the byte rules; reuse it rather than re-implement (a stale
    # duplicate is the guard-input-soundness failure this avoids). It is loaded from its pinned sibling
    # FILE by explicit identity (_load_byte_canon_authority), never a bare `import` that the ambient
    # sys.modules cache could satisfy with a substituted always-clean scanner. Fail closed if it cannot
    # be loaded, or if it lacks the expected interface: cleanliness cannot be asserted without it.
    try:
        byte_canon = _load_byte_canon_authority()
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException as exc:  # noqa: BLE001 - any load failure is fail-closed here
        print("error: cannot load _byte_canon for the byte-canon leg ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2
    if not (isinstance(getattr(byte_canon, "FORBIDDEN", None), dict)
            and callable(getattr(byte_canon, "scan_bytes", None))):
        print("error: the byte-canon authority lacks the expected FORBIDDEN/scan_bytes interface; "
              "fail-closed", file=sys.stderr)
        return 2

    # The forbidden-codepoint set MUST match the authority's, so a body carrying any of them is escaped.
    authority = set(byte_canon.FORBIDDEN.values())
    if set(_FORBIDDEN_CODEPOINTS) != authority:
        failures.append("forbidden-codepoint set disagrees with _byte_canon.FORBIDDEN "
                        "(missing {}, extra {})".format(sorted(authority - set(_FORBIDDEN_CODEPOINTS)),
                                                        sorted(set(_FORBIDDEN_CODEPOINTS) - authority)))

    # sys.modules-substitution pin: the authority is loaded from its pinned sibling FILE, not the ambient
    # sys.modules cache, so a poisoned _byte_canon entry cannot substitute an always-clean scanner
    # and falsely certify the emitted bytes. Poison sys.modules with such a substitute, reload via the
    # loader, and require the reload to still flag a known-forbidden codepoint (U+200B); a bare-import
    # mutant would return the poison and report clean. sys.modules is restored in finally.
    class _AlwaysCleanCanon:
        FORBIDDEN = dict(byte_canon.FORBIDDEN)

        @staticmethod
        def scan_bytes(data):
            return []

    _saved_canon = sys.modules.get("_byte_canon")
    sys.modules["_byte_canon"] = _AlwaysCleanCanon
    try:
        _reloaded = _load_byte_canon_authority()
        if not any("U+200B" in f for f in _reloaded.scan_bytes(chr(0x200B).encode("utf-8"))):
            failures.append("authority-substitution: the byte-canon authority was substituted by a "
                            "poisoned sys.modules entry (an always-clean scanner)")
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException as exc:  # noqa: BLE001 - loading the pinned authority must not fail here
        failures.append("authority-substitution: loading the pinned authority raised {!r}".format(exc))
    finally:
        if _saved_canon is None:
            sys.modules.pop("_byte_canon", None)
        else:
            sys.modules["_byte_canon"] = _saved_canon

    def _byte_canon_clean(text, label):
        findings = byte_canon.scan_bytes(text.encode("utf-8"))
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
    # (2**64 pair-pushes) and does not return promptly. F11: run behind a child-process watchdog so a
    # reverted short-circuit produces a deterministic OOM/TIMEOUT sentinel this assertion rejects rather
    # than HANGING or OOMing the whole self-test in-process; under the guard the child returns "True"
    # instantly.
    shared_sub = {"leaf": 1}
    for _ in range(64):
        shared_sub = {"l": shared_sub, "r": shared_sub}
    _idg = run_bounded(lambda: str(_model_equal([shared_sub, shared_sub], [shared_sub, shared_sub])))
    if _idg != "True":
        failures.append("identity-guard/shared-dag: a shared DAG did not compare equal to itself under the "
                        "identity short-circuit (got {!r}; a regressed short-circuit trips the watchdog)"
                        .format(_idg))

    # ===== ROUND-6 codex: run_bounded watchdog hardening (this is the shared implementation imported by
    # _opf_check and _opf_release, so proving it here holds for all three self-tests) ====================
    import os as _os6
    import signal as _sig6
    import resource as _res6
    import time as _time6

    def _reap_bounded(child):
        """Observe a real subject status; the shared helper owns timeout and cleanup."""
        try:
            while child.poll() is None:
                if _time6.monotonic() >= child.deadline:
                    break
                _time6.sleep(0.005)
        finally:
            child.close()
        return child.status
    # (4) an UNBOUNDED or INVALID control must yield a distinct SETUP-ERROR sentinel WITHOUT running the
    # thunk: timeout_s <= 0 disarms the timer (setitimer(0,0)) and mem_bytes == RLIM_INFINITY / <= 0
    # installs no address-space cap, yet pre-fix the thunk still ran and its result ("RAN") was returned as
    # a clean pass. Reverting the control-validation makes each of these return "RAN".
    if run_bounded(lambda: "RAN", timeout_s=0) != "SETUP-ERROR:BadTimeout":
        failures.append("run_bounded/bad-timeout-zero: timeout_s=0 did not fail closed to SETUP-ERROR")
    if run_bounded(lambda: "RAN", timeout_s=-1) != "SETUP-ERROR:BadTimeout":
        failures.append("run_bounded/bad-timeout-neg: a negative timeout did not fail closed to SETUP-ERROR")
    # A NaN or an infinity cannot arm a finite itimer, so it is rejected PRE-FORK as BadTimeout (never forked
    # to fail setup in the child). Pre-fix these forked and returned a child SETUP-ERROR:<ValueError|Overflow>
    # instead, so asserting the BadTimeout token reds a reverted pre-fork reject (round-15 hygiene).
    if run_bounded(lambda: "RAN", timeout_s=float("nan")) != "SETUP-ERROR:BadTimeout":
        failures.append("run_bounded/bad-timeout-nan: a NaN timeout did not fail closed PRE-FORK to BadTimeout")
    if run_bounded(lambda: "RAN", timeout_s=float("inf")) != "SETUP-ERROR:BadTimeout":
        failures.append("run_bounded/bad-timeout-inf: an infinite timeout did not fail closed PRE-FORK to BadTimeout")
    if run_bounded(lambda: "RAN", mem_bytes=_res6.RLIM_INFINITY) != "SETUP-ERROR:BadMemBound":
        failures.append("run_bounded/bad-mem-infinity: an RLIM_INFINITY mem cap did not fail closed")
    if run_bounded(lambda: "RAN", mem_bytes=0) != "SETUP-ERROR:BadMemBound":
        failures.append("run_bounded/bad-mem-zero: a non-positive mem cap did not fail closed")
    # regression: a VALID control still runs the thunk and returns its token.
    if run_bounded(lambda: "RAN") != "RAN":
        failures.append("run_bounded/valid-control: a valid bounded run did not return the thunk result")

    if hasattr(_os6, "fork"):
        # (5) a child that has bytes buffered in the pipe AND is killed by SIGALRM must return TIMEOUT, not
        # the buffered token: the termination status is inspected FIRST. Build a REAL SIGALRM-signaled
        # wait-status (a child that raises SIGALRM on itself under SIG_DFL) and pair it with a leftover
        # token. Pre-fix (bytes checked first) this returned the token; post-fix the signal wins -> TIMEOUT.
        #
        # The blocked-mask stimulus belongs to an owned process, never this
        # self-test's caller (which may have a live timer or pending SIGALRM).
        def _blocked_status_case():
            _sig6.pthread_sigmask(_sig6.SIG_BLOCK, {_sig6.SIGALRM})

            def _alarm_subject():
                _sig6.signal(_sig6.SIGALRM, _sig6.SIG_DFL)
                _sig6.pthread_sigmask(_sig6.SIG_UNBLOCK, {_sig6.SIGALRM})
                _sig6.raise_signal(_sig6.SIGALRM)
                _sig6.pause()

            _case5 = _FixtureProcess(_time6.monotonic() + 5, subject=_alarm_subject)
            _case5.start()
            _wst5 = _reap_bounded(_case5)
            if not (_os6.WIFSIGNALED(_wst5) and _os6.WTERMSIG(_wst5) == _sig6.SIGALRM):
                return "NOT-SIGALRM"
            return "STATUS:" + _bounded_child_result(b"LEFTOVER-TOKEN", _wst5)

        if run_bounded(_blocked_status_case, timeout_s=15) != "STATUS:TIMEOUT":
            failures.append("run_bounded/status-first: blocked-mask child did not report SIGALRM")
        # regression: a child that exited NORMALLY with bytes returns those bytes (the fix does not swallow
        # a legitimate result).
        _case5b = _FixtureProcess(_time6.monotonic() + 5, subject=lambda: None)
        _case5b.start()
        _wst5b = _reap_bounded(_case5b)
        if _bounded_child_result(b"TOKEN", _wst5b) != "TOKEN":
            failures.append("run_bounded/status-first-normal: a normal-exit child with bytes did not "
                            "return its token")

        # (finding 6) a child that wrote its token then exited NONZERO (exit 7) must NOT have those bytes
        # returned as a clean result: a run_bounded child ALWAYS os._exit(0) after writing, so a nonzero
        # normal exit is an abnormal death. Build a real exit-7 wait-status and pair it with a leftover token;
        # post-fix _bounded_child_result requires WIFEXITED+status 0 and returns CHILD-DIED, pre-fix (only the
        # signal case was checked) it returned the buffered token.
        _case7 = _FixtureProcess(_time6.monotonic() + 5, subject=lambda: _os6._exit(7))
        _case7.start()
        _wst7 = _reap_bounded(_case7)
        if not (_os6.WIFEXITED(_wst7) and _os6.WEXITSTATUS(_wst7) == 7):
            failures.append("run_bounded/nonzero-exit-setup: the fixture child did not exit 7")
        elif _bounded_child_result(b"TOKEN", _wst7) != "CHILD-DIED":
            failures.append("run_bounded/nonzero-exit: a token-then-exit-7 child returned the buffered "
                            "token instead of CHILD-DIED (a successful exit was not required)")

        # (9) the parent must CLOSE the pipe read fd (rfd) after reaping, or every run_bounded call leaks a
        # descriptor. Capture the rfd os.pipe hands out, run one bounded call, and confirm the parent released
        # it. Removing the parent os.close(rfd) leaves it open, which this detects (and then closes so the
        # self-test itself leaks nothing). #378 P1: the leg probes and closes by number only what it can show
        # is still its own. The captured pipe's (st_dev, st_ino) is recorded the moment os.pipe returns, while
        # the call still holds it (a pipe's inode is its own while an end is open, so no other lane's open
        # compares equal), and every close of that number the leg's close stub sees in this process marks it
        # released in a ledger. The number counts as left open only when no close released it AND fstat still
        # returns the identity recorded at its open, and only then is it closed. Each leg runs twice: with the
        # parent's close releasing the number, and with another lane's file put on it instead (dup2 of the
        # leg's own "other-lane" pipe end onto the number at that close, so it is never free for a real lane
        # meanwhile); after that run the number must still name the other-lane pipe (nothing closed it again,
        # the leg's check included), and only then does the leg close it, its own descriptor.
        _pipe_real6 = _os6.pipe
        _close_real6 = _os6.close

        def _st_ident6(fd):
            try:
                _st = _os6.fstat(fd)
            except OSError:
                return None
            return _st.st_dev, _st.st_ino

        def _cap_pipe_into(cap):
            """An os.pipe stand-in recording the rfd it hands out with its identity, taken while still held."""
            def _cap_pipe():
                _r, _w = _pipe_real6()
                try:
                    _st = _os6.fstat(_r)
                except BaseException:
                    _close_real6(_r)                       # never released yet: still this leg's own pipe
                    _close_real6(_w)
                    raise
                cap.update(rfd=_r, wfd=_w, ident=(_st.st_dev, _st.st_ino), released=False)
                return _r, _w
            return _cap_pipe

        def _cap_release6(cap, fd, real_close):
            """The ledger: this process's first close of the captured rfd releases it, or under cap["reuse"]
            puts the other-lane pipe on its number instead. Returns whether it handled fd."""
            if _os6.getpid() != cap["owner"] or fd != cap.get("rfd") or cap.get("released") is not False:
                return False                               # a forked child's close, or not the captured number
            cap["released"] = True
            if cap["reuse"]:
                _os6.dup2(_oth_r6, fd)                     # the number goes straight to another lane's file
            else:
                real_close(fd)
            return True

        def _cap_left_open6(cap):
            """Whether the captured rfd is left open: no close released it and fstat still shows its identity."""
            return cap.get("released") is False and _st_ident6(cap["rfd"]) == cap["ident"]

        def _cap_reuse_kept6(cap, label, key="rfd"):
            """After a reuse run the captured number (cap[key]) must still name the other-lane pipe; the leg then
            closes it, its own descriptor. Returns the failures."""
            if cap.get(key) is None or _st_ident6(cap[key]) != _oth6:
                return ["run_bounded/{}-reused-number: a number another lane reused was closed or replaced "
                        "({})".format(label, cap.get(key))]
            _close_real6(cap[key])
            return []
        _oth_r6, _oth_w6 = _pipe_real6()                   # the other-lane file: this leg's own pipe
        _oth6 = _st_ident6(_oth_r6)
        _cap6 = dict()

        def _cap_close6(fd):
            if not _cap_release6(_cap6, fd, _close_real6):
                _close_real6(fd)
        for _reuse6 in (False, True):
            _tag6 = "-reused" if _reuse6 else ""
            _cap6.clear()
            _cap6.update(reuse=_reuse6, owner=_os6.getpid())
            try:
                _os6.pipe = _cap_pipe_into(_cap6)
                _os6.close = _cap_close6
                _leak_res = run_bounded(lambda: "LEAKCHK")
            finally:
                _os6.pipe = _pipe_real6
                _os6.close = _close_real6
            _leaked6 = _cap_left_open6(_cap6)
            if _leaked6:
                _close_real6(_cap6["rfd"])                 # still this leg's own pipe: close the leak it detected
            if _leak_res != "LEAKCHK":
                failures.append("run_bounded/leak-check-setup{}: the capture run did not return its "
                                "token".format(_tag6))
            if _leaked6:
                failures.append("run_bounded/parent-rfd-leak{}: the parent did not close the pipe read fd "
                                "(a descriptor leaks per call)".format(_tag6))
            elif _reuse6:
                failures += _cap_reuse_kept6(_cap6, "parent-rfd")

        # (finding 7) the parent's wfd close was moved INSIDE the read/cleanup try/finally, so a wfd close that
        # RAISES (OSError EIO) still runs the finally that closes rfd AND reaps the child, rather than leaking
        # the read fd and orphaning the child as a close ahead of the try did. Fault-inject a wfd close that
        # really releases the fd then raises (a hostile teardown close), capturing the pipe fds and the child
        # pid. Post-fix: rfd is released and the child is reaped (waitpid -> ECHILD). Pre-fix: run_bounded
        # skipped both, so rfd stayed open and the child was left unreaped. The rfd is judged as in (9): by the
        # ledger and the identity recorded at the pipe's open, twice, the second run putting the other-lane
        # pipe on the rfd's number at its close, and on the wfd's number at the injected close instead of
        # releasing it (that close's first call only), so a second close of the wfd reaches the real close and
        # closes the other-lane pipe, which the wfd's reused-number check turns red.
        _close_real7 = _os6.close
        _fork_real7 = _os6.fork
        _cap7 = dict()

        def _cap_fork7():
            _p = _fork_real7()
            if _p > 0:
                _cap7["pid"] = _p
            return _p

        def _boom_close7(fd):
            if fd == _cap7.get("wfd") and not _cap7.get("wfd_closed") and _os6.getpid() == _cap7["owner"]:
                _cap7["wfd_closed"] = True
                if _cap7["reuse"]:
                    _os6.dup2(_oth_r6, fd)                 # the number goes straight to another lane's file ...
                else:
                    try:
                        _close_real7(fd)                   # ... or is really released (no leak) ...
                    except OSError:
                        pass
                raise OSError(5, "EIO (self-test injected wfd close)")   # ... then raise, as a hostile close would
            if not _cap_release6(_cap7, fd, _close_real7):
                return _close_real7(fd)
        for _reuse7 in (False, True):
            _tag7 = "-reused" if _reuse7 else ""
            _cap7.clear()
            _cap7.update(reuse=_reuse7, owner=_os6.getpid())
            try:
                _os6.pipe = _cap_pipe_into(_cap7)
                _os6.fork = _cap_fork7
                _os6.close = _boom_close7
                try:
                    run_bounded(lambda: "WFDCHK")          # the wfd close raises; the finally must still run
                except OSError:
                    pass                                   # a propagated teardown OSError is acceptable; cleanup is what matters
            finally:
                _os6.pipe = _pipe_real6
                _os6.fork = _fork_real7
                _os6.close = _close_real7
            if _cap7.get("rfd") is not None and _cap_left_open6(_cap7):
                _close_real7(_cap7["rfd"])                 # still this leg's own pipe: close the leak it detected
                failures.append("run_bounded/wfd-close-raise-rfd-leak{}: a raising parent wfd close skipped the "
                                "rfd cleanup (read fd leaked; finding 7)".format(_tag7))
            elif _reuse7:
                failures += _cap_reuse_kept6(_cap7, "wfd-close-raise-rfd")
            if _reuse7:
                failures += _cap_reuse_kept6(_cap7, "wfd-close-raise-wfd", "wfd")
            _pid7c = _cap7.get("pid")
            if _pid7c is not None and not _fixture_child_reaped(_pid7c):
                failures.append("run_bounded/wfd-close-raise-unreaped-child{}: a raising parent wfd close "
                                "skipped the child reap (zombie left; finding 7)".format(_tag7))
        _close_real6(_oth_r6)
        _close_real6(_oth_w6)

    # (#378 P1, close policy) _finish_close's held-pidfd closes let go of the attribute BEFORE os.close: a close
    # that raises has released the number all the same, so an attribute still naming it would let a later
    # _interrupt_collect -> _address_failed_subject / _escalate signal through a number another lane may hold.
    # Drive _finish_close on a stand-in whose guardian and subject "pidfds" are the read ends of this leg's own
    # pipes, with a close stub that releases each of them on its first close and then raises, and require both
    # attributes None afterwards. The pre-fix close-then-clear left both naming their released numbers. The
    # stub releases each number once; the leg never closes either read end itself, only the write ends it kept.
    import types as _types8
    _close_real8 = _os6.close
    _g_r8, _g_w8 = _os6.pipe()
    _s_r8, _s_w8 = _os6.pipe()
    _armed8 = {_g_r8, _s_r8}
    _released8 = []

    def _boom_close8(fd):
        if fd in _armed8:
            _armed8.discard(fd)
            _released8.append(fd)
            _close_real8(fd)                           # really released, as a raising close counts it ...
            raise OSError(5, "EIO (self-test injected pidfd close)")   # ... then the close raises
        return _close_real8(fd)

    def _noop8():
        return None

    _fake8 = _types8.SimpleNamespace(
        pid=None, collected=True, armed=False, unresolved=False, cleaned=False, _failure=None,
        pidfd=_g_r8, subject_pidfd=_s_r8, subject_pid=None, _recv_subject=_noop8, _interrupt_collect=_noop8,
        control=_types8.SimpleNamespace(close=_noop8), peer=_types8.SimpleNamespace(close=_noop8),
        report=_types8.SimpleNamespace(close=_noop8))
    _raised8 = None
    try:
        _os6.close = _boom_close8
        try:
            _FixtureProcess._finish_close(_fake8)
        except OSError as _exc8:
            _raised8 = _exc8
    finally:
        _os6.close = _close_real8
    if sorted(_released8) != sorted((_g_r8, _s_r8)) or _raised8 is None:
        failures.append("finish-close/raising-close-setup: expected both injected closes to release and raise "
                        "(released {}, raised {!r})".format(_released8, _raised8))
    if _fake8.pidfd is not None:
        failures.append("finish-close/raising-guardian-close: the guardian pidfd attribute still names its "
                        "released number {} (close-then-clear)".format(_fake8.pidfd))
    if _fake8.subject_pidfd is not None:
        failures.append("finish-close/raising-subject-close: the subject pidfd attribute still names its "
                        "released number {} (close-then-clear)".format(_fake8.subject_pidfd))
    _close_real8(_g_w8)
    _close_real8(_s_w8)

    # _model_equal type-strictness pins: the exact-type clause is the sole carrier of the strictness that
    # makes the round-trip proof meaningful rather than merely plausible. A mutant dropping that clause
    # falls back to bare ==, so 1 would equal 1.0 and True would equal 1; these direct assertions turn that
    # mutant red. Only pairs that bare == CONFLATES discriminate the clause: int-vs-float and bool-vs-int
    # (1 == 1.0 and True == 1 are both True under ==). A datetime-vs-date pin is NOT a discriminator and was
    # removed (F12): Python's own == already returns False for a datetime compared to a date, so that
    # assertion passes with OR without the exact-type clause and pins nothing.
    if _model_equal(1, 1.0):
        failures.append("model-equal/int-vs-float: 1 compared equal to 1.0 (exact-type strictness lost)")
    if _model_equal(True, 1) or _model_equal(1, True):
        failures.append("model-equal/bool-vs-int: True compared equal to a bare int")
    if _model_equal({"n": 1}, {"n": 1.0}):
        failures.append("model-equal/nested-int-vs-float: a nested 1 compared equal to 1.0")

    # F8: the reparse boundary binds tomllib.TOMLDecodeError and uses it as an `except` operand. A
    # tomllib-like object whose TOMLDecodeError is NOT an exception class (here the int 7) would make
    # `except _decode_error` raise an uncontrolled TypeError ("catching classes that do not inherit from
    # BaseException") when a reparse failure reaches the handler; _is_exception_spec now rejects it so the
    # failure falls to the value-free EmitError backstop. Discriminates: with the guard reverted the probe
    # escapes as TypeError, not EmitError. The tomllib module attributes carry across into emit_checked (it
    # reads them at call time), restored in a finally.
    _real_tde = tomllib.TOMLDecodeError
    _real_loads = tomllib.loads
    tomllib.TOMLDecodeError = 7                                # a non-exception "decode-error" attribute
    tomllib.loads = lambda _s: (_ for _ in ()).throw(ValueError("f8-forced-reparse-failure"))
    try:
        _f8_kind = None
        try:
            emit_checked({"schema": 1})                       # emits fine; the patched reparse then fails
        except EmitError:
            _f8_kind = "EmitError"
        except BaseException as _exc:                         # noqa: BLE001 - capture an ESCAPING TypeError
            _f8_kind = type(_exc).__name__
    finally:
        tomllib.TOMLDecodeError = _real_tde
        tomllib.loads = _real_loads
    if _f8_kind != "EmitError":
        failures.append("f8/nonexception-tomldecodeerror: a non-exception TOMLDecodeError must fail closed "
                        "to EmitError, not escape as {}".format(_f8_kind))

    # F(cancellation): a substituted tomllib whose loads() raises a CANCELLATION signal (KeyboardInterrupt/
    # SystemExit/GeneratorExit) must RE-RAISE, never be converted to EmitError, even when its TOMLDecodeError
    # is a broad BaseException subclass that `except _decode_error` would otherwise catch. Discriminates the
    # two-part fix: with the cancellation clause moved back BELOW `except _decode_error` AND the
    # Exception-subclass narrowing removed, the signal is swallowed into EmitError and this flips red. The
    # patched tomllib attributes carry into emit_checked (read at call time), restored in a finally.
    for _cancel in (KeyboardInterrupt, SystemExit, GeneratorExit):
        _real_tde2 = tomllib.TOMLDecodeError
        _real_loads2 = tomllib.loads
        tomllib.TOMLDecodeError = _cancel                     # a cancellation-class "decode-error" spec
        tomllib.loads = (lambda _c: (lambda _s: (_ for _ in ()).throw(_c())))(_cancel)
        try:
            _c_kind = "no-raise"
            try:
                emit_checked({"schema": 1})                   # emits fine; the patched reparse then cancels
            except EmitError:
                _c_kind = "EmitError"
            except _cancel:                                   # the required re-raise of genuine control flow
                _c_kind = "reraised"
            except BaseException as _exc:                     # noqa: BLE001 - capture any other escape
                _c_kind = type(_exc).__name__
        finally:
            tomllib.TOMLDecodeError = _real_tde2
            tomllib.loads = _real_loads2
        if _c_kind != "reraised":
            failures.append("cancellation/reparse: a {} raised by a substituted loads must re-raise, not "
                            "become {}".format(_cancel.__name__, _c_kind))

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

    # emit_checked backstop coverage (round-2): the decode-error handler must not itself escape on a
    # malformed injected tomllib, and the model-equivalence COMPARISON (not merely the reparse) must be
    # enforced and fail closed when it raises. Each leg turns red on the specific mutant named; hermetic,
    # rebound via globals() and restored in finally.
    class _NoDecodeAttrReparse:  # a tomllib LACKING TOMLDecodeError: the except clause must not raise
        def loads(self, text):
            raise RecursionError("stubbed reparse failure; this tomllib lacks TOMLDecodeError")

    globals()["tomllib"] = _NoDecodeAttrReparse()
    try:
        try:
            emit_checked(dict(a=1))
            failures.append("emit-checked/missing-decode-type: a reparse failure under a tomllib lacking "
                            "TOMLDecodeError was not converted to a fail-closed EmitError")
        except EmitError:
            pass
        except BaseException as exc:  # noqa: BLE001 - anything but EmitError is the fail-open escape
            failures.append("emit-checked/missing-decode-type: escaped as {!r} instead of a fail-closed "
                            "EmitError".format(exc))
    finally:
        globals()["tomllib"] = _saved_tomllib

    class _HostileDecodeError(Exception):  # a decode error whose __str__ is hostile
        def __str__(self):
            raise RuntimeError("a hostile decode-error __str__ must never be formatted into a diagnostic")

    class _HostileStrReparse:
        TOMLDecodeError = _HostileDecodeError

        def loads(self, text):
            raise _HostileDecodeError()

    globals()["tomllib"] = _HostileStrReparse()
    try:
        try:
            emit_checked(dict(a=1))
            failures.append("emit-checked/hostile-decode-str: a hostile decode-error __str__ path did not "
                            "fail closed to EmitError")
        except EmitError:
            pass
        except BaseException as exc:  # noqa: BLE001 - a leaked RuntimeError is the fail-open escape
            failures.append("emit-checked/hostile-decode-str: escaped as {!r} instead of a fail-closed "
                            "EmitError".format(exc))
    finally:
        globals()["tomllib"] = _saved_tomllib

    # The model-equivalence comparison is enforced (a mutant deleting `if not _model_equal(...)` returns
    # unproven text) and fails closed when it raises. Swap _model_equal for a False stub (require the
    # round-trip EmitError) and for a MemoryError stub (require the generic backstop EmitError).
    _saved_model_equal = globals()["_model_equal"]
    try:
        globals()["_model_equal"] = lambda _a, _b: False
        try:
            emit_checked(dict(a=1))
            failures.append("emit-checked/compare-enforced: a False model-equivalence comparison did not "
                            "fail closed (unproven text returned)")
        except EmitError as exc:
            if "round-trip" not in str(exc):
                failures.append("emit-checked/compare-enforced: rejected but not by the round-trip "
                                "comparison ({})".format(exc))

        def _raise_memoryerror(_a, _b):
            raise MemoryError("stubbed comparison failure for the backstop pin")

        globals()["_model_equal"] = _raise_memoryerror
        try:
            emit_checked(dict(a=1))
            failures.append("emit-checked/compare-backstop: a MemoryError in the comparison was not "
                            "converted to a fail-closed EmitError")
        except EmitError:
            pass
        except BaseException as exc:  # noqa: BLE001 - anything but EmitError is the fail-open escape
            failures.append("emit-checked/compare-backstop: a comparison MemoryError escaped as {!r} "
                            "instead of a fail-closed EmitError".format(exc))
    finally:
        globals()["_model_equal"] = _saved_model_equal

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
    # PIN the int-str-conversion limit to the default (4300) around the reject sweep (test-hermeticity): the
    # "oversized-int" fixture (10 ** 4301, a 4302-digit int) is rejected fail-closed ONLY because str() of it
    # trips CPython's base-10 digit limit, so a hostile ambient of 0 (unlimited) or 5001 would render it as a
    # valid TOML integer and it would be accepted (breaking reject/oversized-int). At the pinned 4300 str()
    # trips, so the emitter's guard is exercised and reverting it lets the raw ValueError escape (caught by
    # the loop's except). Pinning to 4300 is the CPython default, so it is a no-op for every other fixture in
    # the sweep (none of which constructs an over-limit int). Restored in finally.
    _rej_prev_idlimit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        for name, document in rejects.items():
            try:
                if not _rejects(document):
                    failures.append("reject/{}: was accepted but is outside the subset".format(name))
            except Exception as exc:  # noqa: BLE001 - a non-EmitError is a fail-open escape, a defect
                failures.append("reject/{}: raised {!r} instead of a fail-closed EmitError".format(name, exc))
    finally:
        sys.set_int_max_str_digits(_rej_prev_idlimit)

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
    # output ceiling as a backstop: assert each raises an EmitError that NAMES a cyclic reference. With that
    # cycle check removed, these instead grow the dotted header until _MAX_EMIT_BYTES raises a
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
    # PIN the int-str-conversion limit to the default (4300) here for the same reason as the reject sweep:
    # 10 ** 4301 (4302 digits) trips str()'s base-10 limit only at or below the default, so a hostile ambient
    # of 0 (unlimited) or 5001 would render it cleanly and the guard would never fire. Restored in finally.
    _ovm_prev_idlimit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        try:
            emit({"k": 10 ** 4301})
            failures.append("oversized-int-message: an oversized int was not rejected")
        except EmitError as exc:
            if "too large to render" not in str(exc):
                failures.append("oversized-int-message: rejected, but not by the specific oversized-int guard "
                                "({})".format(exc))
    finally:
        sys.set_int_max_str_digits(_ovm_prev_idlimit)

    # CLI mode selection validates the WHOLE argument vector, not mere membership: exactly one recognized
    # flag runs the self-test, and anything else (an unknown or extra token, a duplicated flag, a bare
    # positional, or an empty vector) is misuse. A membership regression (`"--self-test" in args`) would
    # let a malformed control vector read as a valid self-test run, so these turn that regression red.
    for good_argv in (["--self-test"],):
        if _selected_mode(good_argv) != "self-test":
            failures.append("cli/valid: {!r} was not recognized as a self-test invocation".format(good_argv))
    for bad_argv in ([], ["--self-test", "--unknown"], ["--self-test", "--self-test"],
                     ["--selftest"], ["--selftest", "extra"], ["--self-t"], ["positional"],
                     ["--self-test", "--selftest"]):
        if _selected_mode(bad_argv) != "misuse":
            failures.append("cli/misuse: {!r} was not classified as misuse (membership, not whole-vector, "
                            "validation)".format(bad_argv))

    # CLI mode selection validates the vector is an exact list of exact strings BEFORE any equality
    # comparison, so a hostile str subclass injected into argv cannot raise from mode selection or spoof
    # a self-test invocation. A membership/`in` regression would run the subclass __eq__: one that raises
    # would leak, one that always returns True would spoof a self-test run.
    class _RaisingEqStr(str):
        def __eq__(self, other):
            raise RuntimeError("a hostile argv __eq__ must never be reached by mode selection")

        def __hash__(self):
            return id(self)

    class _AlwaysEqStr(str):
        def __eq__(self, other):
            return True

        def __hash__(self):
            return id(self)

    try:
        if _selected_mode([_RaisingEqStr("--malformed")]) != "misuse":
            failures.append("cli/hostile-eq: a raising-__eq__ argv token was not classified as misuse")
    except Exception as exc:  # noqa: BLE001 - a leaked comparison is the fail-open escape
        failures.append("cli/hostile-eq: mode selection leaked {!r} instead of classifying misuse".format(exc))
    if _selected_mode([_AlwaysEqStr("--malformed")]) != "misuse":
        failures.append("cli/spoof-eq: an always-equal argv token spoofed a self-test invocation")

    # emit_checked returns exactly what emit returns for a good document (no divergent second path).
    if emit_checked(coverage) != emit(coverage):
        failures.append("emit_checked/parity: emit_checked text differs from emit text")

    # #378 P1: the guardian's subject-pidfd close, green, and red by REUSE alone under the pre-fix body.
    failures.extend(_st_guardian_close_reuse())

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: round-trip fuzz over adversarial bodies, canonical-form determinism, the "
          "constrained-subset accepted and rejected shapes, byte-canon cleanliness (verified against "
          "_byte_canon), arbitrary-depth iterative emission and equality (depth {}), cyclic-"
          "reference rejection, shared-DAG acceptance, the output-ceiling bound, and the golden byte "
          "vector all hold".format(iterative_depth))
    return 0


def _selected_mode(args):
    """Map a CLI argument vector to a mode. The WHOLE vector is validated, not mere membership: exactly
    `["--self-test"]` selects 'self-test' (no alias, no prefix), and any other vector (an unknown or extra
    argument, a duplicated flag, a bare positional, or an empty vector) is 'misuse', so a malformed control vector is
    never silently read as a valid self-test invocation. The vector must be an exact list of exact `str`
    tokens; a non-list, or a token that is not exactly `str` (a hostile str subclass whose `__eq__` could
    raise or always match), is 'misuse' before any equality comparison runs."""
    if type(args) is not list or not all(type(a) is str for a in args):
        return "misuse"
    if args == ["--self-test"]:
        return "self-test"
    return "misuse"


def main():
    if _selected_mode(sys.argv[1:]) == "self-test":
        return self_test()
    print("usage: _opf_emit.py --self-test (a library module; no live mode)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
