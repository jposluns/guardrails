#!/usr/bin/env python3
"""OPF import batch parser (OPF-SPEC 2.0.0 sections 8.3 and 8.8): pure, canonical and INERT. No command or
writer calls parse_batch in this release; it ships ahead of the import write mode (`opf record import
--batch`), its one gate caller is check_opf_prompt_pack.py, which parses the prompt pack's import
examples read-only, and it writes nothing: parse_batch performs no I/O (no file, clock, environment,
network or process; importing the module resolves its own file path to reach its sibling modules, and the
self-test and CLI print their verdicts; the self-test builds its byte vectors in memory).

parse_batch(data) parses ONE `opf.record.import-batch/v1` document, the batch the spec 8.8 import surface
takes: "`import --batch FILE [--root DIR]`: the primary import surface", where "One invocation covers one
source in one journaled transaction". The format:

  schema = "opf.record.import-batch/v1"   the one format this unit understands (any other is fail-closed)
  [source]                                exactly one source per batch; `path` is the legacy file's own
    path = "notes/old.md"                 product-relative canonical path (the store file-operand grammar,
                                          never absolute, never `..`, never inside a control area)
  [[record]]                              imported full-envelope rows; `key` is the batch-local key and
    key = "r1"                            `type` a baseline record type
  [[worklog]]                             imported worklog rows; their type is fixed by the array

Each row carries the imported record's OWN content (title, status, historical timestamps, type fields,
`unrecorded` missingness rows, and the provenance content `span`, `history` and `unparsed`, which the
builder moves into the record's `import` table). The writer-authored members are REFUSED in a row: `id`
(the writer resolves local keys to claimed imported IDs, spec 8.8), `actor` (the importing actor is run
evidence) and `import` (provenance binding is the writer's). Caller-supplied measurements are REFUSED
anywhere they appear, in the source table and in a row alike: spec 8.8, the writer "MUST recompute source
size and SHA-256 from preserved bytes, never trusting caller-supplied measurements".

CANONICAL FORM. A batch is canonical when its bytes equal its own canonical re-emission: `schema` first,
then `[source]`, then every `[[record]]`, then every `[[worklog]]`; in each table the `key` line first,
then `type`, then the remaining keys sorted; every value inline (a basic string with the fixed escape set,
a decimal integer, `true`/`false`, an array, or an inline table with sorted keys); LF line ends and one
blank line before each table header. A comment, another key order, another quoting and any other
re-spelling is NOT canonical and the batch is refused, so one source history has one byte form.
batch_digest is the SHA-256 of the canonical bytes, the batch half of the spec 8.8 replay identity:
"Replay identity is `(source_sha256, batch content digest)`." The value grammar is deliberately small
(strings, integers in TOML's signed 64-bit range, booleans, arrays, tables, nested at most
MAX_CANON_DEPTH deep): a float, a native TOML date-time or an out-of-range integer has no canonical batch
form in v1 (timestamps are RFC 3339 UTC strings, as the imported envelope requires), and a batch over
MAX_BATCH_BYTES is refused unread. An ITERATIVE preflight enforces the depth and integer bounds on the
parsed document before any copy, row validation or emission, and a preflight refusal is reported ALONE,
so once the schema is identified no recursive helper and no value formatting ever sees a value past the
bounds (the schema-mismatch refusal itself renders the foreign `schema` value first, through the
iterative, bounded _opf_schema._bounded_display; no unbounded formatting exists on any path).

VALIDATION. Each row is built into an imported record and checked with _opf_schema.validate_imported_record
(IS-0): the builder assigns per-namespace PLACEHOLDER ids (`imported:<NS>-0<n>` in batch order: a spelling
the spec 8.2 id grammar refuses outright, since `[1-9][0-9]*` admits no leading zero, so NO real imported
id, an earlier import run's included, can ever spell a placeholder; placeholders are never allocations,
spec 8.8 keeps the one allocation seam in the writer, whose FUTURE obligation, once the import write mode
lands, is to replace each placeholder with a freshly allocated `imported:<NS>-<n>` inside its journaled
transaction and re-validate there; that contract is POSITIONAL: the writer replaces placeholders only at
the GENERATED REFERENCE POSITIONS, the row's own `id` and each key-resolved `links[].id`, located
structurally from the parsed rows, never by scanning text for the spelling, and preserves every other
string verbatim, so a title, a history row's `value`, a refs locator or note, or an `unparsed` entry may
carry placeholder-LOOKING text and the batch stays VALID with those bytes returned byte-identical (a
`scopes` entry spelling a minted placeholder is refused, so no third position ever holds
one)), the placeholder importer actor, and a placeholder `import` table whose `source` is the
preserved-original path the record's own run will hold. The validator judges each record under its
placeholder's well-formed MASK (`imported:<NS>-<n>`, the placeholder minus its leading zero, substituted
at exactly the record-id-bearing positions), so it sees the real envelope, link and scope shape while the
returned Batch carries only placeholder spellings; each returned validator finding is re-labelled under
the row's placeholder, so no finding names a row by a spelling a real store record could carry. A
`links` row targets either another row by its batch-local `key`, which the builder resolves to that
row's placeholder id (dangling and duplicate keys are refused), or an existing imported-series id
verbatim, which ALWAYS passes through: a well-formed id can never spell a placeholder, so a key link
and a verbatim link can never resolve to the same id in the result, and a verbatim link to an id an
earlier import run allocated is never refused for colliding with a placeholder. A verbatim id that
SPELLS a placeholder this batch itself mints is a genuine alias of a batch row and is refused by
name, with no second finding (a link to this batch's own row must use its
local key); a clean-series id IN A LINK is refused by the validator ("Imported-to-clean links MUST be
refused by both writer and doctor", spec 8.6), a links-only refusal: a block row's `scopes` entry may
name either series (spec 8.1/8.2: either series may be scoped, with an imported scope id held to the
imported namespaces), so a well-formed clean-series scope passes and a malformed scopes entry draws the
validator's own shape finding, never the spec 8.6 link refusal. The same ALIASING rule covers every
record-id-bearing row member, and
`links[].id` and a block row's `scopes` entries are the ONLY two, enumerated against the schema: the
record's own `id` is writer-authored and refused in a row, `refs` rows carry {kind, locator, note}
locators and notes (free text, never typed record references), `unrecorded` rows carry schema FIELD
names, a `history` row carries a schema field name and a verbatim source `value` string (preserved
source text, never a typed record reference, spec 8.3), `actor.id` names an actor, and no
other baseline member holds a record id. A `scopes` entry spelling a minted placeholder is refused by
name (scopes resolve no batch-local key: a scope names a record that already exists in the store, never a
row of its own batch). The importer-only
legacy_fragment type is refused (spec 8.1/8.2: new imports use the imported series, never LF
quarantine), and a module-tier type is refused in this release (the first
imported-series release supports baseline types only).

Outcome model (single-sourced from _opf_store): CANNOT-EVALUATE for an input this unit cannot read under
v1 assumptions (not exactly bytes, over the byte bound, not UTF-8, not TOML, an over-long integer
literal, another or absent `schema`, nesting past the canonical depth bound, or a validator control
failure), never a pass; INVALID for a parsed batch that violates the format (a row finding names its row,
and collection stops early in three places: a preflight refusal is reported alone, a missing or unusable
source path prevents row building, so such a batch builds and validates no row (staging still grades
every row's key and type, the writer-authored and measurement refusals still fire, and any other source
finding, a caller-supplied measurement in [source] included, leaves row building intact), and a row whose
TYPE is refused at staging is not
built, while a row refused for its KEY alone is still built under its placeholder and validated, so its
remaining findings are still collected); VALID
otherwise, and only then does the result carry a Batch. batch_digest(data) returns the digest only for a
VALID batch, so no replay key is ever minted from a batch THIS PARSER refuses; the writer re-validates
every built record and can still refuse a parser-VALID batch (e.g. against its own run clock, spec 8.8).

NOT CHECKED HERE (the writer and the doctor own these): everything validate_imported_record discloses it
does not check; the run-level spec 8.8 refusals (the adoption receipt, plan scope, bound-item drift, the
recomputed source size and digest, replay lookup, the type being enabled in the manifest, the run clock
bound on historical timestamps, which a pure parser has no clock for: the placeholder `imported_at` is the
latest RFC 3339 instant, so the writer MUST re-validate against its own run clock); that `source.path`
names a migrate-disposed source inside the approved plan scope; that a local-key link is acyclic or
non-self-referential; and whether a verbatim imported-series link or block-scopes target exists (C-LINKS resolves the
union, spec 8.3).

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules, so the standalone-closure property holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_import_batch.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import copy
import hashlib
import re
import tomllib
from pathlib import Path

# Required for direct execution under python3 -I, including an opf-only copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_schema as schema  # noqa: E402
import _opf_store as store    # noqa: E402

VALID, INVALID, CANNOT_EVALUATE = store.VALID, store.INVALID, store.CANNOT_EVALUATE

# The one batch format this unit understands; any other `schema` value is fail-closed, never parsed
# under v1 assumptions (the _opf_schema SUPPORTED_SCHEMA stance).
BATCH_SCHEMA = "opf.record.import-batch/v1"
# Inclusive byte bound on the whole batch document, applied before decoding (a disclosed capacity
# limit, not a process sandbox): one batch covers one source's history, and the journal's own
# contained-read cap is 16 MiB, so a 4 MiB batch document is already generous.
MAX_BATCH_BYTES = 4 * 1024 * 1024
# Inclusive nesting bound of the canonical value grammar (a table or array that is a row member sits at
# depth 1). tomllib parses far deeper, so the bound is the format's, not the parser's: past it the
# canonical emission refuses fail-closed rather than rendering by unbounded recursion.
MAX_CANON_DEPTH = 100
# TOML's integer value range (signed 64-bit): tomllib parses arbitrary precision, so the bound is the
# format's, not the parser's. The preflight refuses an out-of-range value before any copy, validation or
# emission (CPython's integer-string digit limit would otherwise surface as a bare ValueError).
INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1
# The closed top-level keyset, in canonical emission order.
TOP_KEYS = ("schema", "source", "record", "worklog")
SOURCE_KEYS = frozenset({"path"})
# Caller-supplied measurements, refused wherever they appear (spec 8.8: the writer recomputes source
# size and SHA-256 from preserved bytes, "never trusting caller-supplied measurements").
MEASUREMENT_KEYS = ("bytes", "sha256", "size", "source_sha256")
# The writer-authored record members a batch row must not carry (spec 8.8: the writer resolves local
# keys to claimed imported IDs; the importing actor and the provenance binding are run evidence).
WRITER_KEYS = ("actor", "id", "import")
# Provenance CONTENT the batch supplies and the builder moves into the record's `import` table
# (spec 8.3: optional `span`, `history` rows and `unparsed`); the binding members stay the writer's.
PROVENANCE_CONTENT_KEYS = ("history", "span", "unparsed")
# The batch-local key grammar: short, lower-case and link-safe.
LOCAL_KEY_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
# Control areas no import source may name (a legacy source lives in the product tree, never under
# version control's or the store's own machinery), derived from the store's own constants so the two
# lists cannot drift: the store-root control dirs (.git and .aiqt), the store working directory and the
# two store pointer names. ALL are refused at ANY depth: the store's own exclusions are anchored at the
# STORE ROOT (see _opf_store.STORE_ROOT_CONTROL_DIRS), and a store root need not be the product root, so
# a batch's product-relative path cannot tell which subtree roots a store; a control name anywhere is
# some potential store root's machinery, and this inert parser refuses it fail-closed (the writer, which
# resolves the real store root, owns the final plan-scope judgement).
_CONTROL_COMPONENTS = frozenset(n.casefold() for n in store.STORE_ROOT_CONTROL_DIRS + (
    store.WORKING_DIRNAME, store.POINTER_REL, store.LOCAL_POINTER_REL))
# An ALIASED spelling of a control name is a refused input, never a different destination. The compare
# folds the alias spellings git itself treats as `.git` (its verify_path: an NTFS or HFS+ lookup
# reaches the directory through them), applied to every control name alike:
#   - case (on a case-insensitive filesystem .GIT names .git), folded by casefold();
#   - NTFS: an alternate-data-stream suffix (`.git::` U+0024 `INDEX_ALLOCATION` opens the directory
#     itself, so everything from the first colon is cut), trailing dots and spaces (`.git.` and
#     `.git ` resolve to `.git`), and the 8.3 short name (`GIT~1`; matched as the control name's DOS
#     stem, dots dropped and cut to six characters, `~` with a digit run, and the three-character
#     extension where the name carries one, e.g. `OPF~1.TOM` for `.opf.toml`; a LONGER digit run cuts
#     the stem further, so the numeric-tail name keeps 8.3's eight characters: `WORKIN~1`,
#     `WORKI~10`, `WORK~100` and so on down to a one-character stem);
#   - HFS+: the ignorable code points an HFS+ name lookup skips (U+200C..U+200F, U+202A..U+202E,
#     U+206A..U+206F and U+FEFF), dropped before the compare.
# DISCLOSED, deliberately left open beside this check: it closes the git-documented LEXICAL alias set,
# not every filesystem equivalence. Unicode NFC/NFD normalization and single-script confusables (a
# dotless U+0131 in place of the i of .git) are not folded; NTFS hash-mangled short names (the
# `GI7EBA~1` style) are not enumerated; a trailing-dot, trailing-space or stream alias of a
# NON-control name stays a plain (odd) product path, the store file-operand grammar's business (a
# RESIDUAL of that grammar, recorded here, not changed here: a `..` or `...` component with a
# trailing space, `.. /x`, passes that grammar as an odd plain name although Win32 normalization
# strips the trailing space and can resolve it as a parent reference; the control-area refusal
# still applies at any depth); and
# filesystem-level aliasing (links, mounts, case-insensitive lookups of whole trees) is no lexical
# check's to close. The writer re-judges the real path against its own plan scope and store root
# (spec 8.8).
_HFS_IGNORABLE = frozenset(chr(c) for c in (
    tuple(range(0x200C, 0x2010)) + tuple(range(0x202A, 0x202F))
    + tuple(range(0x206A, 0x2070)) + (0xFEFF,)))


def _dos_short_pattern(name):
    """The NTFS 8.3 short-name pattern of one control name: the DOS stem (the name before its last
    dot, or the whole name when nothing precedes that dot, dots and other non-alphanumerics dropped,
    cut to six), `~` with a digit run, and the cut extension where the name carries one (`.git` to
    `git~N`, `.opf.toml` to `opf~N.tom`). A digit run of d digits cuts the stem to 7 - d characters,
    so the numeric-tail name keeps 8.3's eight before the extension (`WORKIN~1`, `WORKI~10`,
    `WORK~100`, down to a one-character stem); the full-stem arm keeps ANY digit run, fail-closed,
    since an over-long spelling such as `git~12345` aliases nothing real but gains nothing as a
    source path either."""
    base, dot, ext = name.rpartition(".")
    if not dot or not base:
        base, ext = name, ""
    stem = re.sub(r"[^a-z0-9]", "", base.casefold())[:6]
    tail = re.sub(r"[^a-z0-9]", "", ext.casefold())[:3]
    ext_re = "\\." + re.escape(tail) if tail else ""
    arms = [re.escape(stem) + "~[0-9]+"]
    for run in range(2, 7):
        cut = 7 - run
        if 0 < cut < len(stem):
            arms.append("{}~[0-9]{{{}}}".format(re.escape(stem[:cut]), run))
    return "(?:{})".format("|".join(arms)) + ext_re


_CONTROL_SHORT_RE = re.compile(
    "(?:" + "|".join(sorted({_dos_short_pattern(n) for n in _CONTROL_COMPONENTS})) + ")\\Z")


def _alias_component(comp):
    """The spelling a case-, NTFS- and HFS+-aliasing lookup compares one path component under (the
    fold the _CONTROL_COMPONENTS comment derives): HFS+-ignorable code points dropped, an NTFS stream
    suffix cut at the first colon, NTFS-ignored trailing dots and spaces dropped, then casefolded."""
    comp = "".join(ch for ch in comp if ch not in _HFS_IGNORABLE)
    comp = comp.split(":", 1)[0]
    return comp.rstrip(". ").casefold()


def _names_control_area(comp):
    """True when one path component names a control area under any folded alias spelling, the 8.3
    short names included."""
    folded = _alias_component(comp)
    return folded in _CONTROL_COMPONENTS or _CONTROL_SHORT_RE.match(folded) is not None


# PLACEHOLDERS: the fixed stand-ins validation runs under. None is an allocation or a clock read; the
# writer replaces every one of them inside its journaled transaction and re-validates there.
# The run id satisfies the store's own import-run grammar (shape, not calendar, as the store checks it).
PLACEHOLDER_RUN = "imp-00010101T000000Z-0000000000000000"
PLACEHOLDER_SOURCE_SHA256 = "0" * 64
# The LATEST RFC 3339 UTC instant: a pure parser has no clock, so the structural check that a historical
# timestamp is no later than `imported_at` (spec 8.3) cannot bind here; the writer re-validates every
# built record against its own run clock (spec 8.8 refuses "a historical timestamp later than the run
# clock"), where the real `imported_at` replaces this.
PLACEHOLDER_IMPORTED_AT = "9999-12-31T23:59:59Z"
PLACEHOLDER_ACTOR_ID = "importer-unassigned"
# The preserved-original prefix every built record's import.source carries, derived from the store's own
# evidence-home constructor so the two grammars cannot drift (import of this module raises if they do).
PLACEHOLDER_ORIGINALS = store.evidence_run("import", PLACEHOLDER_RUN) + "/originals/"
# Placeholder ids are spelled `imported:<NS>-0<n>` (per namespace, in batch order): the id grammar is
# `imported:<NS>-<n>` with n drawn from `[1-9][0-9]*` (spec 8.2, _opf_schema.parse_series_id), which
# admits NO leading zero, so no allocated id, this run's or any earlier run's, can ever spell a
# placeholder, and a verbatim id in a row can never alias a batch row by accident. The writer still
# allocates the real id. Its FUTURE obligation (the spec 8.8 allocation seam; no writer ships in this
# release, and THIS parser performs no replacement) is POSITIONAL: replace each placeholder with a
# freshly allocated `imported:<NS>-<n>` inside its journaled transaction and re-validate there, only
# at the GENERATED REFERENCE POSITIONS the parsed rows locate structurally, the row's own `id` and
# each key-resolved `links[].id` (a minted spelling in `scopes` is refused, so no third position
# holds one), NEVER by scanning strings for the spelling: the spelling is refused only at
# record-id positions, and a title, a history row's `value`, a refs locator or note, or an
# `unparsed` entry carrying placeholder-LOOKING text is preserved verbatim. Validation in
# THIS parser runs under _validation_mask's well-formed mask, so the validator never sees the
# placeholder spelling at an id position.
_PLACEHOLDER_RE = re.compile(r"imported:([A-Z]{2})-0([1-9][0-9]*)\Z")


def _mint_placeholder(ns, n):
    """The nth placeholder of namespace `ns`: `imported:<NS>-0<n>`, a spelling parse_series_id refuses
    (no real id carries a leading zero, spec 8.2), so a placeholder and a real id can never collide."""
    return "{}{}-0{}".format(schema.IMPORTED_PREFIX, ns, n)


def _validation_mask(placeholder):
    """The well-formed `imported:<NS>-<n>` id validation runs under for one placeholder (the
    placeholder minus its leading zero), or None for a string that is not a placeholder spelling."""
    m = _PLACEHOLDER_RE.match(placeholder)
    if m is None:
        return None
    return "{}{}-{}".format(schema.IMPORTED_PREFIX, m.group(1), m.group(2))


_NOT_BYTES = "import batch must be exactly bytes (fail-closed)"
_MEASUREMENT_REFUSAL = ("carries the caller-supplied measurement {!r}: the writer recomputes source size "
                        "and SHA-256 from preserved bytes, never trusting caller-supplied measurements "
                        "(spec 8.8)")


class BatchRow:
    """One built row: `record` is the imported record validate_imported_record judged, under the
    placeholder id, actor and import table."""
    __slots__ = ("key", "kind", "type", "placeholder_id", "record")

    def __init__(self, key, kind, rtype, placeholder_id, record):
        self.key = key
        self.kind = kind                      # "record" or "worklog"
        self.type = rtype
        self.placeholder_id = placeholder_id  # imported:<NS>-0<n>, a placeholder, never an allocation
        self.record = record


class Batch:
    __slots__ = ("source_path", "rows", "local_ids", "canonical", "digest")

    def __init__(self, source_path, rows, local_ids, canonical, digest):
        self.source_path = source_path
        self.rows = rows            # tuple of BatchRow, records first, in batch order
        self.local_ids = local_ids  # {batch-local key: placeholder id}
        self.canonical = canonical  # the canonical bytes (equal to the input of a VALID parse)
        self.digest = digest        # SHA-256 hex of the canonical bytes (spec 8.8 replay identity half)


class BatchValidation:
    __slots__ = ("status", "findings", "batch")

    def __init__(self, status, findings=None, batch=None):
        self.status = status        # VALID / INVALID / CANNOT_EVALUATE
        self.findings = findings or []
        self.batch = batch          # a Batch only when status is VALID


class _CanonError(Exception):
    """A document the canonical emitter refuses: `status` says how the batch grades (INVALID for a value
    outside the v1 grammar, CANNOT-EVALUATE for one past a resource bound), fail-closed either way."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# --- canonical emission --------------------------------------------------------------------------------

_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
# Code points escaped beyond the named short escapes: DEL, every C1 control (NEL among them), the
# non-LF line boundaries splitlines knows (LS, PS), the bidirectional format characters (ALM, LRM,
# RLM, the embedding/override set and the isolate set) and the zero-width set, each emitted as a
# backslash-u escape so no emitted line is split by a data character and no code point of THIS
# decided set lands raw in a canonical batch. DECIDED, not merely disclosed: U+200B
# (ZWSP), U+200C (ZWNJ), U+200D (ZWJ) and U+FEFF (ZWNBSP/BOM) are ESCAPED, since an invisible
# zero-width character spoofs a reader exactly as a bidirectional control does and a mid-string BOM
# survives a naive re-decode; the self-test enumerates the whole required set independently of this
# table, so shrinking it turns a vector red.
# DISCLOSED, deliberately NOT escaped in this release (each stays raw, canonical data): the other
# invisible or format code points with the same spoofing character, U+00AD (SOFT HYPHEN), U+180E
# (MONGOLIAN VOWEL SEPARATOR), U+2060 (WORD JOINER), the invisible operators U+2061..U+2064, the
# interlinear annotation controls U+FFF9..U+FFFB and the tag block U+E0001..U+E007F. Widening the
# escape set re-spells every canonical batch that carries one of them (the replay digest moves), so
# that widening is a decided format revision, never a quiet table edit.
_EXTRA_ESCAPED = (frozenset((0x7F, 0x2028, 0x2029, 0x061C, 0x200E, 0x200F))
                  | frozenset((0x200B, 0x200C, 0x200D, 0xFEFF))  # zero-width: ZWSP, ZWNJ, ZWJ, BOM
                  | frozenset(range(0x80, 0xA0))       # the C1 controls, NEL included
                  | frozenset(range(0x202A, 0x202F))   # LRE, RLE, PDF, LRO, RLO
                  | frozenset(range(0x2066, 0x206A)))  # LRI, RLI, FSI, PDI

_STR_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _canon_string(s):
    """The one canonical TOML basic-string spelling: the named short escapes, backslash-u (upper-case hex)
    for every other C0 and C1 control character, for the non-LF line boundaries splitlines knows (NEL, LS,
    PS, so no emitted line is ever split by a data character), for the bidirectional format characters,
    for the zero-width set (ZWSP, ZWNJ, ZWJ, ZWNBSP/BOM), and every other code point raw UTF-8."""
    out = ['"']
    for ch in s:
        esc = _STR_ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
        elif ord(ch) < 0x20 or ord(ch) in _EXTRA_ESCAPED:
            out.append("\\u{:04X}".format(ord(ch)))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _canon_key(key):
    return key if _BARE_KEY_RE.match(key) else _canon_string(key)


def _canon_value(value, depth):
    """The canonical inline spelling of one value, recursing at most MAX_CANON_DEPTH levels. Only types a
    tomllib model carries reach this (parse_batch parses first), so key types need no re-check; bool is
    judged before int, since an isinstance int check would admit it."""
    if depth > MAX_CANON_DEPTH:
        raise _CanonError(CANNOT_EVALUATE, "the batch nests values deeper than {} levels, past the "
                          "canonical v1 grammar (fail-closed)".format(MAX_CANON_DEPTH))
    t = type(value)
    if t is str:
        return _canon_string(value)
    if t is bool:
        return "true" if value else "false"
    if t is int:
        if value < INT64_MIN or value > INT64_MAX:
            # The preflight already refused this; a backstop for a direct caller. The message never
            # formats the value (an over-long str() is itself refused by CPython's digit limit).
            raise _CanonError(INVALID, "an integer outside TOML's signed 64-bit range has no canonical "
                              "batch form (fail-closed)")
        return str(value)
    if t is list:
        return "[" + ", ".join(_canon_value(m, depth + 1) for m in value) + "]"
    if t is dict:
        return "{" + ", ".join("{} = {}".format(_canon_key(k), _canon_value(value[k], depth + 1))
                               for k in sorted(value)) + "}"
    raise _CanonError(INVALID, "a {} value has no canonical batch form (v1 values are strings, integers, "
                      "booleans, arrays and tables; a timestamp is an RFC 3339 UTC string, "
                      "spec 8.3)".format(t.__name__))


def _canon_table_lines(table, fixed_first=()):
    order = [k for k in fixed_first if k in table]
    order += sorted(k for k in table if k not in fixed_first)
    return "".join("{} = {}\n".format(_canon_key(k), _canon_value(table[k], 1)) for k in order)


def canonical_batch_bytes(doc):
    """The canonical emission of a parsed batch document (see the module docstring). Raises _CanonError on
    a document outside the emittable shape or value grammar; parse_batch grades that refusal, and a
    direct caller must treat it as a refused batch, never emit a best effort."""
    if type(doc) is not dict or type(doc.get("source")) is not dict:
        raise _CanonError(INVALID, "the batch has no canonical form: it is not a table holding the "
                          "[source] table")
    for kind in ("record", "worklog"):
        rows = doc.get(kind, [])
        if type(rows) is not list or any(type(row) is not dict for row in rows):
            raise _CanonError(INVALID, "the batch has no canonical form: [[{}]] is not an array of "
                              "tables".format(kind))
    pieces = ["schema = {}\n".format(_canon_value(doc.get("schema"), 1))]
    pieces.append("\n[source]\n" + _canon_table_lines(doc["source"]))
    for kind in ("record", "worklog"):
        for row in doc.get(kind, []):
            pieces.append("\n[[{}]]\n".format(kind) + _canon_table_lines(row, fixed_first=("key", "type")))
    return "".join(pieces).encode("utf-8")


# --- parsing -------------------------------------------------------------------------------------------

def _source_path_error(path):
    """None when `path` is a legal source path, else the finding: the store's canonical file-operand
    grammar (no absolute, drive or backslash form, no empty, '.' or '..' component, no control
    character), spelled from the product root and outside every control area (the store control dirs,
    the working directory name and the store pointer names, each at ANY depth and under any folded
    alias spelling; see _CONTROL_COMPONENTS and _names_control_area)."""
    if type(path) is not str:
        return "source.path must be a string (the legacy file's product-relative path)"
    try:
        store._home_file(path)
    except ValueError:
        return ("source.path {} is not a canonical product-relative file path (no absolute, drive or "
                "backslash form, no empty, '.' or '..' component, no control character)".format(
                    schema._bounded_display(path)))
    if any(_names_control_area(c) for c in path.split("/")):
        return ("source.path {} lies inside a control area ({} at any depth, case, NTFS and HFS+ alias "
                "spellings included): a legacy source lives in the product tree".format(
                    schema._bounded_display(path), ", ".join(sorted(_CONTROL_COMPONENTS))))
    return None


def _first_difference(a, b):
    """The index of the first differing byte (the shorter length when one is a prefix of the other)."""
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def _preflight_error(doc):
    """The ITERATIVE depth and integer-range preflight over the whole parsed document, run before any
    copy, row validation or canonical emission, so no recursive helper (deepcopy, the validator, the
    emitter) and, once the schema is identified, no value formatting ever sees a value past the bounds
    (the preceding schema-mismatch refusal renders the foreign `schema` value through the iterative,
    bounded _opf_schema._bounded_display, the only formatting on any earlier path). A preflight refusal
    is returned ALONE, one finding, before staging or the validator can add another, and the self-test
    pins that aloneness, so the preflight itself is discriminated from the emitter backstop (a removed
    or depth-limited preflight check turns those vectors red). Depth counts exactly as the
    emitter counts it (a member of the top level, of [source] or of a row sits at depth 1, and the bound
    refuses a value AT a depth past MAX_CANON_DEPTH, scalar or container alike); a shape the emitter
    refuses without walking is still walked here, strict side, so a refusal can only move earlier, never
    later. Returns the refusing BatchValidation, or None for a document within both bounds."""
    stack = []
    for key, value in doc.items():
        if key == "source" and type(value) is dict:
            stack.extend((v, 1) for v in value.values())
        elif key in ("record", "worklog") and type(value) is list:
            for row in value:
                if type(row) is dict:
                    stack.extend((v, 1) for v in row.values())
                else:
                    stack.append((row, 1))
        else:
            stack.append((value, 1))
    while stack:
        value, depth = stack.pop()
        if depth > MAX_CANON_DEPTH:
            return BatchValidation(CANNOT_EVALUATE, [
                "the batch nests values deeper than {} levels, past the canonical v1 grammar "
                "(fail-closed)".format(MAX_CANON_DEPTH)])
        t = type(value)
        if t is int and (value < INT64_MIN or value > INT64_MAX):
            return BatchValidation(INVALID, [
                "an integer outside TOML's signed 64-bit range ({} to {}) has no canonical batch form "
                "(fail-closed)".format(INT64_MIN, INT64_MAX)])
        if t is list:
            stack.extend((m, depth + 1) for m in value)
        elif t is dict:
            stack.extend((m, depth + 1) for m in value.values())
    return None


def _closed_keys(table, where, allowed, findings):
    """The closed-keyset walk shared by the top level and the source table, with the dedicated
    measurement refusal first, so a caller-supplied digest or size is named as such (spec 8.8), never as
    a mere unknown key."""
    for key in table:
        if key in allowed:
            continue
        if key in MEASUREMENT_KEYS:
            findings.append("{} {}".format(where, _MEASUREMENT_REFUSAL.format(key)))
        else:
            findings.append("{} unknown key {} (the batch schema is closed)".format(
                where, schema._bounded_display(key)))


def _resolve_links(content, where, local_ids, minted, findings):
    """Resolve each {rel, key} link in a FRESH copy of a row's links to {rel, id} through the batch-local
    key table (spec 8.8: the writer resolves local keys to claimed imported IDs; here they resolve to the
    same rows' placeholder ids, so the validator judges the real link shape under the placeholders'
    masks). A {rel, id} link passes through verbatim for the validator's own imported-series judgement (a
    clean-series id is ITS refusal), ALWAYS when the id is well-formed: no real id can spell a
    placeholder, so a verbatim link to an id an earlier run allocated is never refused and a key link
    and a verbatim link can never resolve to the same id in the result. The ONE refusal left is a
    GENUINE alias: an id that spells a placeholder this batch itself mints (`minted` holds every staged
    placeholder id), refused by name, since a link to this batch's own row must use its local key; the
    link passes through unchanged so _masked_for_validation re-spells it and the refusal stays the
    single finding for that link. A link with both members, with neither, or with a dangling or
    non-string key is a finding. A links member that is not a list, or a link that is not a table,
    passes through for the validator's own shape findings."""
    links = content.get("links")
    if not isinstance(links, list):
        return
    resolved = []
    for i, link in enumerate(links):
        if not isinstance(link, dict):
            resolved.append(link)
            continue
        has_key, has_id = "key" in link, "id" in link
        if has_key == has_id:
            findings.append("{} links[{}] must carry exactly one of key (a batch-local key) or id (an "
                            "imported-series id), has {}".format(
                                where, i, "both" if has_key else "neither"))
            resolved.append({k: v for k, v in link.items() if k != "key"})
            continue
        if not has_key:
            vid = link.get("id")
            if type(vid) is str and vid in minted:
                findings.append("{} links[{}].id {} spells a placeholder this batch itself mints, a "
                                "genuine alias of a batch row: a link to this batch's own row must use "
                                "its local key (no real imported id can spell a placeholder, so a "
                                "well-formed verbatim id always names a store row and is never "
                                "refused, spec 8.8)".format(where, i, schema._bounded_display(vid)))
            resolved.append(link)
            continue
        lk = link.get("key")
        if type(lk) is not str or lk not in local_ids:
            findings.append("{} links[{}].key {} names no row of this batch (a dangling batch-local "
                            "key)".format(where, i, schema._bounded_display(lk)))
            resolved.append({k: v for k, v in link.items() if k != "key"})
            continue
        row_link = {k: v for k, v in link.items() if k != "key"}
        row_link["id"] = local_ids[lk]
        resolved.append(row_link)
    content["links"] = resolved


def _refuse_minted_scopes(content, where, minted, findings):
    """The links aliasing rule applied to a block row's `scopes`, the ONLY other record-id-bearing row
    member (enumerated in the module docstring: the record's own `id` is writer-authored and refused,
    `refs` rows carry locators and notes, `unrecorded` rows carry schema field names, a `history` row
    carries a schema field name and a verbatim source `value` string, `actor.id` names an actor, and
    no other baseline member holds a record id): a scopes entry spelling a placeholder this batch
    itself mints is a genuine alias of a batch row and is refused by name; scopes resolve no
    batch-local key, so a verbatim entry names a record that already exists in the store, and no
    real id can spell a placeholder. The entry stays in place for _masked_for_validation, so the
    refusal is the single finding for that entry. A scopes member that is not a list, and a
    non-string entry, pass through for the validator's own shape findings."""
    scopes = content.get("scopes")
    if type(scopes) is not list:
        return
    for i, entry in enumerate(scopes):
        if type(entry) is str and entry in minted:
            findings.append("{} scopes[{}] {} spells a placeholder this batch itself mints: a scope "
                            "names a record that already exists in the store, never a row of its own "
                            "batch (spec 8.5/8.8)".format(where, i, schema._bounded_display(entry)))


def _masked_for_validation(record, minted):
    """A FRESH copy of a built record with every minted placeholder re-spelled as its well-formed
    _validation_mask, at exactly the record-id-bearing positions (the record's own `id`, each
    `links[].id` and each `scopes` entry), so validate_imported_record judges the real envelope, link
    and scope shape without ever seeing the unspellable placeholder form; the stored BatchRow keeps
    the placeholder spelling, which the writer's future obligation replaces at these same generated
    reference positions (spec 8.8), preserving every other string verbatim."""
    masked = copy.deepcopy(record)
    if type(masked.get("id")) is str and masked.get("id") in minted:
        masked["id"] = _validation_mask(masked["id"])
    links = masked.get("links")
    if type(links) is list:
        for link in links:
            if type(link) is dict and type(link.get("id")) is str and link.get("id") in minted:
                link["id"] = _validation_mask(link["id"])
    scopes = masked.get("scopes")
    if type(scopes) is list:
        masked["scopes"] = [_validation_mask(s) if type(s) is str and s in minted else s
                            for s in scopes]
    return masked


def parse_batch(data):
    """Parse and validate one batch document (see the module docstring). Returns a BatchValidation; its
    `batch` is a Batch only when the status is VALID, so a refused batch never yields rows or a digest.
    Pure: no file, clock, environment, network or process is read or touched."""
    if type(data) is not bytes:
        # Exactly bytes, by type identity, and the refusal is one fixed message that never runs a method
        # of the refused object (the sibling entry-gate stance): a bytearray, a bytes subclass or a
        # spoofing carrier refuses unread.
        return BatchValidation(CANNOT_EVALUATE, [_NOT_BYTES])
    if len(data) > MAX_BATCH_BYTES:
        return BatchValidation(CANNOT_EVALUATE, [
            "import batch is {} bytes, over the {} byte bound (fail-closed)".format(
                len(data), MAX_BATCH_BYTES)])
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return BatchValidation(CANNOT_EVALUATE, ["import batch is not UTF-8 (fail-closed): {}".format(exc)])
    try:
        doc = tomllib.loads(text)
    except (ValueError, RecursionError, MemoryError) as exc:
        # TOMLDecodeError is a ValueError; the bare ValueError also covers tomllib refusing an
        # over-long integer literal at CPython's digit limit, graded the same way (fail-closed).
        return BatchValidation(CANNOT_EVALUATE, ["import batch is not parseable TOML (fail-closed): "
                                                 "{}".format(schema._bounded_display(str(exc)))])
    if doc.get("schema") != BATCH_SCHEMA:
        return BatchValidation(CANNOT_EVALUATE, [
            "import batch schema {} is not {!r}: another format is never parsed under v1 assumptions "
            "(fail-closed)".format(schema._bounded_display(doc.get("schema")), BATCH_SCHEMA)])
    refused = _preflight_error(doc)
    if refused is not None:
        return refused

    findings = []
    _closed_keys(doc, "batch", frozenset(TOP_KEYS), findings)

    # Exactly one source per batch (spec 8.8: one invocation covers one source); TOML cannot spell two
    # [source] tables, so presence and shape are the whole check.
    src = doc.get("source")
    source_path = None
    if type(src) is not dict:
        findings.append("missing required table: [source] (one source per batch, spec 8.8)")
    else:
        _closed_keys(src, "source", SOURCE_KEYS, findings)
        if "path" not in src:
            findings.append("missing required field: source.path")
        else:
            err = _source_path_error(src.get("path"))
            if err is not None:
                findings.append(err)
            else:
                source_path = src.get("path")

    rows_in = []
    for kind in ("record", "worklog"):
        arr = doc.get(kind, [])
        if not isinstance(arr, list):
            findings.append("{} must be an array of tables".format(kind))
            continue
        for i, row in enumerate(arr):
            where = "{}[{}]".format(kind, i)
            if not isinstance(row, dict):
                findings.append("{} must be a table".format(where))
                continue
            rows_in.append((kind, where, row))
    if not rows_in:
        findings.append("the batch holds no record and no worklog row: an import batch records at least "
                        "one row for its source (an empty history is a recorded skip, not a batch; "
                        "spec 14.1)")

    # First pass: batch-local keys, row types and placeholder ids, so links can resolve forward and
    # backward alike. Placeholder numbering is per namespace in batch order, spelled outside the id
    # grammar by _mint_placeholder; it is NEVER an allocation (spec 8.8 keeps the one allocation seam
    # in the writer).
    local_ids = {}
    counts = {}
    staged = []
    for kind, where, row in rows_in:
        key = row.get("key")
        if "key" not in row:
            findings.append("{} missing required field: key (the batch-local key)".format(where))
            key = None
        elif type(key) is not str or LOCAL_KEY_RE.match(key) is None:
            findings.append("{} key {} is not a batch-local key ([a-z0-9][a-z0-9-]*, at most 64 "
                            "characters)".format(where, schema._bounded_display(key)))
            key = None
        elif key in local_ids:
            findings.append("{} key {!r} repeats an earlier row's key: batch-local keys are "
                            "unique".format(where, key))
            key = None
        rtype = None
        if kind == "record":
            t = row.get("type")
            if "type" not in row or not isinstance(t, str):
                findings.append("{} missing or non-string required field: type".format(where))
            elif t == "worklog":
                findings.append("{} a worklog row belongs in [[worklog]], never in [[record]]".format(where))
            elif t in store.IMPORTER_TYPES:
                findings.append("{} type {!r} is the importer-only LF quarantine type, which the "
                                "imported series refuses (spec 8.1/8.2)".format(where, t))
            elif t in store.MODULE_TYPES:
                findings.append("{} type {!r} is a module-tier type: the first imported-series release "
                                "supports baseline types only (decision D6)".format(where, t))
            elif t not in schema.BASELINE_SPECS:
                findings.append("{} type {} is not a supported record type (baseline: {})".format(
                    where, schema._bounded_display(t), ", ".join(sorted(schema.BASELINE_SPECS))))
            else:
                rtype = t
        else:
            if "type" in row:
                findings.append("{} a worklog row's type is fixed by its array and never "
                                "spelled".format(where))
            else:
                rtype = "worklog"
        placeholder = None
        if rtype is not None:
            ns = schema.BASELINE_SPECS[rtype].namespace
            counts[ns] = counts.get(ns, 0) + 1
            placeholder = _mint_placeholder(ns, counts[ns])
            if key is not None:
                local_ids[key] = placeholder
        staged.append((kind, where, row, key, rtype, placeholder))

    # Second pass: refuse writer-authored and measurement members, then build each well-staged row into
    # an imported record under the placeholders and judge it with validate_imported_record (IS-0).
    cannot = []
    batch_rows = []
    minted = frozenset(p for _kind, _where, _row, _key, _rtype, p in staged if p is not None)
    import_source = None if source_path is None else PLACEHOLDER_ORIGINALS + source_path
    for kind, where, row, key, rtype, placeholder in staged:
        for k in sorted(row, key=schema._bounded_display):
            if k in MEASUREMENT_KEYS:
                findings.append("{} {}".format(where, _MEASUREMENT_REFUSAL.format(k)))
            elif k in WRITER_KEYS:
                findings.append("{} carries the writer-authored member {!r}: the writer assigns ids, the "
                                "importing actor and the import provenance binding (spec 8.8)".format(
                                    where, k))
        if rtype is None or placeholder is None or import_source is None:
            continue  # the refusal above or the source finding already grades the batch
        content = {k: copy.deepcopy(v) for k, v in row.items()
                   if k not in ("key", "type") and k not in WRITER_KEYS
                   and k not in MEASUREMENT_KEYS and k not in PROVENANCE_CONTENT_KEYS}
        _resolve_links(content, where, local_ids, minted, findings)
        _refuse_minted_scopes(content, where, minted, findings)
        record = {"id": placeholder, "type": rtype}
        record.update(content)
        record["actor"] = {"kind": "importer", "id": PLACEHOLDER_ACTOR_ID}
        provenance = {"source": import_source, "source_sha256": PLACEHOLDER_SOURCE_SHA256,
                      "run": PLACEHOLDER_RUN, "imported_at": PLACEHOLDER_IMPORTED_AT}
        for k in PROVENANCE_CONTENT_KEYS:
            if k in row:
                provenance[k] = copy.deepcopy(row[k])
        record["import"] = provenance
        verdict = schema.validate_imported_record(_masked_for_validation(record, minted),
                                                   expected_type=rtype)
        # The validator labels a finding by the id it judged, the MASK, which is exactly a spelling
        # a real store record could carry; re-label it under the row's placeholder (a spelling
        # outside the id grammar), so no returned finding names a row by a real-id spelling.
        mask_label = "imported record {}".format(
            schema._bounded_display(_validation_mask(placeholder)))
        row_label = "imported record {}".format(schema._bounded_display(placeholder))
        relabelled = [row_label + f[len(mask_label):] if f.startswith(mask_label) else f
                      for f in verdict.findings]
        if verdict.status == CANNOT_EVALUATE:
            # Unreachable for a tomllib model under the default controls, but a validator that cannot
            # evaluate a built record must never read as a mere finding (fail-closed).
            cannot.extend("{}: {}".format(where, f) for f in relabelled)
            continue
        findings.extend("{}: {}".format(where, f) for f in relabelled)
        batch_rows.append(BatchRow(key, kind, rtype, placeholder, record))

    canonical = None
    try:
        canonical = canonical_batch_bytes(doc)
    except _CanonError as exc:
        if exc.status == CANNOT_EVALUATE:
            cannot.append(str(exc))
        else:
            findings.append(str(exc))
    if canonical is not None and canonical != data:
        findings.append("the batch is not canonical: its canonical re-emission differs from the input "
                        "at byte {} (re-emitted bytes must equal the input; see the module "
                        "docstring)".format(_first_difference(canonical, data)))

    if cannot:
        return BatchValidation(CANNOT_EVALUATE, findings + cannot)
    if findings:
        return BatchValidation(INVALID, findings)
    digest = hashlib.sha256(canonical).hexdigest()
    return BatchValidation(VALID, [], Batch(source_path, tuple(batch_rows), dict(local_ids),
                                            canonical, digest))


def batch_digest(data):
    """The SHA-256 hex digest of a VALID batch's canonical bytes (the batch half of the spec 8.8 replay
    identity), or None for any batch parse_batch refuses: no replay key is ever minted from a batch
    THIS PARSER refuses (the writer re-validates and can still refuse a parser-VALID batch, spec 8.8)."""
    result = parse_batch(data)
    return result.batch.digest if result.status == VALID else None


# --- self-test -------------------------------------------------------------------------------------------

# The hand-pinned canonical fixture: one backlog_item record linking its own worklog row by batch-local
# key. BYTE-EXACT on purpose: the acceptance vector pins the canonical form itself, so an emitter change
# that re-spells the format turns it red rather than silently moving the replay digest.
_FIXTURE = """\
schema = "opf.record.import-batch/v1"

[source]
path = "notes/old.md"

[[record]]
key = "r1"
type = "backlog_item"
links = [{key = "w1", rel = "relates"}]
status = "done"
title = "Old backlog item"
unrecorded = [{field = "created_at", reason = "not_recorded_in_source"}, {field = "updated_at", reason = "not_recorded_in_source"}]

[[worklog]]
key = "w1"
status = "recorded"
summary = "Imported the old item note"
title = "Imported worklog entry"
unrecorded = [{field = "date", reason = "not_recorded_in_source"}, {field = "detail", reason = "not_recorded_in_source"}, {field = "kind", reason = "not_recorded_in_source"}]
""".encode("utf-8")

# A second hand-pinned fixture for the EMITTER rules the acceptance fixture does not reach: the
# backslash-u escape set with UPPER-CASE hex (ESC, DEL, NEL, another C1 control, LS, PS and a bidi
# override), both boolean spellings, and quoted keys (a non-bare row key and a non-bare inline-table
# key). BYTE-EXACT on purpose, like _FIXTURE (a raw string, so the TOML escapes stay literal):
# re-emitting its own parse must reproduce these bytes, so an emitter re-spelling (a raw control,
# True/False, lower-case hex, an unquoted key) turns red.
_EMITTER_FIXTURE = r"""schema = "opf.record.import-batch/v1"

[source]
path = "notes/old.md"

[[record]]
key = "r1"
type = "backlog_item"
flag = true
"odd key" = {"a.b" = "\u001B\u007F\u0085\u009B\u2028\u2029\u202E", on = false}
""".encode("utf-8")


def _edited(old, new):
    """The fixture with `old` replaced once by `new`; a missing needle is a harness error, never a
    silently unedited vector."""
    text = _FIXTURE.decode("utf-8")
    if old not in text:
        raise ValueError("fixture needle not found: {!r}".format(old))
    return text.replace(old, new, 1).encode("utf-8")


def _refuses(result, status, text):
    """The vector contract for a refused batch: the expected status, a finding carrying `text`, and no
    Batch (so no digest and no rows escape a refusal)."""
    return (result.status == status and result.batch is None
            and any(text in f for f in result.findings))


def _capped_parse(data, kind):
    """parse_batch and batch_digest of the data while _opf_store._StKeyPartCap injects a counting
    subclass of kind (RecursionError or MemoryError) for a key past the emulated cap: returns
    (injections, result, digest). An injected exception escaping parse_batch returns (injections,
    None, None), a failed vector rather than a suite abort."""
    fired = []

    class Injected(kind):
        def __init__(self, *args):
            super().__init__(*args)
            fired.append(kind)

    try:
        with store._StKeyPartCap(Injected):
            result = parse_batch(data)
            digest = batch_digest(data)
    except Injected:
        return len(fired), None, None
    return len(fired), result, digest


def self_test():
    """Pure byte vectors, judged on returned statuses, findings and bytes: no file, process, network or
    clock. Exit 0 all pass, 1 a failed check, 2 a harness error (fail-closed)."""
    results = []

    def check(name, ok):
        results.append((name, bool(ok)))

    try:
        bi = store.BASELINE_TYPES["backlog_item"]
        wl = store.BASELINE_TYPES["worklog"]
        base = parse_batch(_FIXTURE)
        check("canonical-accepted", base.status == VALID and base.findings == []
              and base.batch is not None)
        batch = base.batch
        check("batch-model", batch.source_path == "notes/old.md" and batch.canonical == _FIXTURE
              and [(r.kind, r.type, r.key, r.placeholder_id) for r in batch.rows]
              == [("record", "backlog_item", "r1", "imported:{}-01".format(bi)),
                  ("worklog", "worklog", "w1", "imported:{}-01".format(wl))]
              and batch.local_ids == {"r1": "imported:{}-01".format(bi),
                                      "w1": "imported:{}-01".format(wl)})
        record = batch.rows[0].record
        check("record-built-under-placeholders",
              record["actor"] == {"kind": "importer", "id": PLACEHOLDER_ACTOR_ID}
              and record["import"]["source"] == PLACEHOLDER_ORIGINALS + "notes/old.md"
              and record["import"]["run"] == PLACEHOLDER_RUN
              and record["import"]["source_sha256"] == PLACEHOLDER_SOURCE_SHA256
              and record["import"]["imported_at"] == PLACEHOLDER_IMPORTED_AT
              and record["links"] == [{"rel": "relates", "id": batch.local_ids["w1"]}])
        minted_base = frozenset(r.placeholder_id for r in batch.rows)
        check("rows-revalidate-valid",
              all(schema.validate_imported_record(_masked_for_validation(r.record, minted_base),
                                                  expected_type=r.type).status == VALID
                  for r in batch.rows))
        # The placeholder spelling itself is pinned OUTSIDE the id grammar: parse_series_id refuses
        # every minted placeholder (no real id can spell one, so no verbatim id can ever alias a
        # batch row), while each placeholder's validation mask parses as the well-formed id its
        # leading zero was stripped from.
        check("placeholder-unspellable",
              all(schema.parse_series_id(p) is None for p in minted_base)
              and all(schema.parse_series_id(_validation_mask(p)) is not None for p in minted_base))
        independent = hashlib.sha256(_FIXTURE).hexdigest()
        check("digest-deterministic", batch.digest == independent
              and batch_digest(_FIXTURE) == independent and batch_digest(_FIXTURE) == independent)
        check("canonical-fixed-point",
              canonical_batch_bytes(tomllib.loads(_FIXTURE.decode("utf-8"))) == _FIXTURE)

        # Non-canonical re-spellings of the SAME model: each refused, naming the first differing byte,
        # and no digest is minted (red on a disabled canonical comparison).
        respelled = (
            ("comment", b"# c\n" + _FIXTURE),
            ("key-order", _edited('status = "done"\ntitle = "Old backlog item"\n',
                                  'title = "Old backlog item"\nstatus = "done"\n')),
            ("spacing", _edited('key = "r1"', 'key =  "r1"')),
            ("quoting", _edited('path = "notes/old.md"', "path = 'notes/old.md'")),
            ("crlf", _FIXTURE.replace(b"\n", b"\r\n", 1)),
        )
        for name, data in respelled:
            got = parse_batch(data)
            check("non-canonical-" + name + "-refused",
                  _refuses(got, INVALID, "not canonical") and batch_digest(data) is None)
        check("non-canonical-names-byte", _refuses(parse_batch(b"# c\n" + _FIXTURE), INVALID, "at byte 0"))

        # Entry gates: exactly bytes, by type identity, with one fixed finding that never reads the
        # refused object.
        class _Spoof(bytes):
            pass

        for name, value in (("str", _FIXTURE.decode("utf-8")), ("bytearray", bytearray(_FIXTURE)),
                            ("bytes-subclass", _Spoof(_FIXTURE)), ("none", None)):
            got = parse_batch(value)
            check("entry-gate-" + name, got.status == CANNOT_EVALUATE and got.findings == [_NOT_BYTES]
                  and got.batch is None)

        # The inclusive byte bound: at the bound parses, one byte over refuses unread.
        filler = "a" * (MAX_BATCH_BYTES - len(_FIXTURE))
        at_bound = _edited("Old backlog item", "Old backlog item" + filler)
        check("at-byte-bound-accepted", len(at_bound) == MAX_BATCH_BYTES
              and parse_batch(at_bound).status == VALID)
        over = _FIXTURE + b"#" * (MAX_BATCH_BYTES + 1 - len(_FIXTURE))
        check("over-byte-bound-cannot", len(over) == MAX_BATCH_BYTES + 1
              and _refuses(parse_batch(over), CANNOT_EVALUATE, "byte bound"))

        # Unreadable inputs are CANNOT-EVALUATE, never a pass and never a crash.
        check("not-utf8-cannot", _refuses(parse_batch(bytes((255, 254))), CANNOT_EVALUATE, "not UTF-8"))
        check("not-toml-cannot", _refuses(parse_batch(b"= broken\n"), CANNOT_EVALUATE, "not parseable"))
        check("other-schema-cannot", _refuses(parse_batch(_edited("import-batch/v1", "import-batch/v2")),
                                              CANNOT_EVALUATE, "never parsed under v1"))
        check("missing-schema-cannot", _refuses(parse_batch(b"x = 1\n"), CANNOT_EVALUATE,
                                                "never parsed under v1"))

        # Closed keysets: the top level directly, a row through the validator's own closed-schema rule.
        got = parse_batch(_FIXTURE + b'\n[extra]\nx = 1\n')
        check("unknown-top-key-refused", got.status == INVALID
              and any("batch unknown key" in f and "extra" in f for f in got.findings))
        got = parse_batch(_edited('status = "done"\n', 'nonsuch = "x"\nstatus = "done"\n'))
        check("unknown-row-key-refused", got.status == INVALID
              and any("record[0]:" in f and "unknown key" in f for f in got.findings))

        # Caller-supplied measurements are refused by name, in the source table and in a row alike.
        sha_line = 'source_sha256 = "{}"\n'.format("0" * 64)
        got = parse_batch(_edited('path = "notes/old.md"\n', 'path = "notes/old.md"\n' + sha_line))
        check("caller-digest-in-source-refused",
              _refuses(got, INVALID, "caller-supplied measurement 'source_sha256'"))
        got = parse_batch(_edited('path = "notes/old.md"\n', 'path = "notes/old.md"\nsize = 5\n'))
        check("caller-size-in-source-refused",
              _refuses(got, INVALID, "caller-supplied measurement 'size'"))
        got = parse_batch(_edited('status = "done"\n', 'sha256 = "ab"\nstatus = "done"\n'))
        check("caller-digest-in-row-refused",
              _refuses(got, INVALID, "record[0] carries the caller-supplied measurement 'sha256'"))

        # Writer-authored members are refused by name in a row.
        for member, line in (("id", 'id = "XX-1"\n'), ("actor", 'actor = {id = "x", kind = "importer"}\n'),
                             ("import", 'import = {run = "x"}\n')):
            got = parse_batch(_edited('key = "r1"\ntype = "backlog_item"\n',
                                      'key = "r1"\ntype = "backlog_item"\n' + line))
            check("writer-key-" + member + "-refused",
                  _refuses(got, INVALID, "writer-authored member {!r}".format(member)))

        # Batch-local keys and links: duplicates, dangling keys, the exactly-one-member rule, the
        # clean-series refusal (the validator's, spec 8.6) and a verbatim imported-series target.
        got = parse_batch(_edited('key = "w1"\nstatus = "recorded"', 'key = "r1"\nstatus = "recorded"'))
        check("duplicate-local-key-refused", got.status == INVALID
              and any("repeats an earlier row's key" in f for f in got.findings))
        got = parse_batch(_edited('{key = "w1", rel = "relates"}', '{key = "nope", rel = "relates"}'))
        check("dangling-local-key-refused", got.status == INVALID
              and any("dangling batch-local key" in f for f in got.findings))
        got = parse_batch(_edited('{key = "w1", rel = "relates"}',
                                  '{id = "imported:' + wl + '-1", key = "w1", rel = "relates"}'))
        check("link-both-members-refused", got.status == INVALID
              and any("exactly one of key" in f and "has both" in f for f in got.findings))
        got = parse_batch(_edited('[{key = "w1", rel = "relates"}]', '[{rel = "relates"}]'))
        check("link-neither-member-refused", got.status == INVALID
              and any("has neither" in f for f in got.findings))
        got = parse_batch(_edited('{key = "w1", rel = "relates"}',
                                  '{id = "' + bi + '-3", rel = "relates"}'))
        check("link-clean-id-refused", got.status == INVALID
              and any("clean-series id" in f for f in got.findings))
        verbatim = parse_batch(_edited('{key = "w1", rel = "relates"}',
                                       '{id = "imported:' + bi + '-9", rel = "relates"}'))
        check("link-imported-id-verbatim-valid", verbatim.status == VALID
              and verbatim.batch.rows[0].record["links"] == [{"id": "imported:" + bi + "-9",
                                                              "rel": "relates"}])
        # MAJOR-1 (round 2): a verbatim link to a REAL imported id an EARLIER run allocated is never
        # refused, even to imported:<NS>-1, the id the old `imported:<NS>-<n>` placeholder scheme
        # collided with: placeholders now live outside the id grammar, so imported:<NS>-1 always
        # names a store row, never this batch's own row, and the link rides through with no finding.
        existing = parse_batch(_edited('{key = "w1", rel = "relates"}',
                                       '{id = "imported:' + bi + '-1", rel = "relates"}'))
        check("link-existing-id-never-refused", existing.status == VALID
              and existing.batch.rows[0].record["links"] == [{"id": "imported:" + bi + "-1",
                                                              "rel": "relates"}]
              and "imported:" + bi + "-1" not in {r.placeholder_id for r in existing.batch.rows})
        # The same verbatim link in a ONE-ROW batch: VALID, one result per row, no refusal and no
        # spurious second finding.
        one_head = _FIXTURE.decode("utf-8").split("\n[[worklog]]", 1)[0]
        one_row = one_head.replace('links = [{key = "w1", rel = "relates"}]',
                                   'links = [{id = "imported:' + bi + '-1", rel = "relates"}]',
                                   1).encode("utf-8")
        got = parse_batch(one_row)
        check("link-existing-id-one-row-valid", got.status == VALID and got.findings == []
              and len(got.batch.rows) == 1
              and got.batch.rows[0].record["links"] == [{"id": "imported:" + bi + "-1",
                                                         "rel": "relates"}])
        # A verbatim id that SPELLS a minted placeholder is the one GENUINE alias left: refused by
        # name, for the linking row's own placeholder and for a sibling row's alike, and refused
        # ALONE (the link passes to the validator under its mask, so no spurious `links[0].id None`
        # or malformed-id finding rides along; the imported:<NS>-9 and imported:<NS>-1 vectors above
        # are the just-outside cases).
        got = parse_batch(_edited('{key = "w1", rel = "relates"}',
                                  '{id = "imported:' + bi + '-01", rel = "relates"}'))
        check("link-own-placeholder-refused", _refuses(got, INVALID, "must use its local key")
              and len(got.findings) == 1
              and not any("not a well-formed id" in f for f in got.findings))
        got = parse_batch(_edited('{key = "w1", rel = "relates"}',
                                  '{id = "imported:' + wl + '-01", rel = "relates"}'))
        check("link-sibling-placeholder-refused", _refuses(got, INVALID, "must use its local key")
              and len(got.findings) == 1
              and not any("not a well-formed id" in f for f in got.findings))

        # Row types: the LF quarantine, a module-tier type (decision D6), an unknown type, and the two
        # worklog placements.
        got = parse_batch(_edited('type = "backlog_item"', 'type = "legacy_fragment"'))
        check("lf-type-refused", _refuses(got, INVALID, "importer-only LF quarantine type"))
        module_type = sorted(store.MODULE_TYPES)[0]
        got = parse_batch(_edited('type = "backlog_item"', 'type = "{}"'.format(module_type)))
        check("module-type-refused", _refuses(got, INVALID, "baseline types only"))
        got = parse_batch(_edited('type = "backlog_item"', 'type = "nonsuch"'))
        check("unknown-type-refused", _refuses(got, INVALID, "not a supported record type"))
        got = parse_batch(_edited('type = "backlog_item"', 'type = "worklog"'))
        check("record-row-worklog-type-refused", _refuses(got, INVALID, "belongs in [[worklog]]"))
        got = parse_batch(_edited('key = "w1"\n', 'key = "w1"\ntype = "worklog"\n'))
        check("worklog-row-type-key-refused", _refuses(got, INVALID, "fixed by its array"))

        # The source table and the batch shape: non-canonical paths; control-area paths (every
        # control name at any depth, case-, NTFS- and HFS+-aliased and pointer-file spellings
        # alike); the just-outside names that stay plain product paths; the absent path; the absent
        # table; and the empty batch.
        for name, bad, text in (
                ("dotdot", "../x", "not a canonical product-relative file path"),
                ("absolute", "/etc/x", "not a canonical product-relative file path"),
                ("control-area", ".working/x", "lies inside a control area"),
                ("store-area", ".aiqt/x", "lies inside a control area"),
                (".git-area", ".git/x", "lies inside a control area"),
                ("git-case", ".GIT/config", "lies inside a control area"),
                ("git-mixed-case", ".Git/x", "lies inside a control area"),
                ("store-area-case", ".AIQT/x", "lies inside a control area"),
                ("working-case", ".Working/x", "lies inside a control area"),
                ("pointer", ".opf.toml", "lies inside a control area"),
                ("local-pointer", ".opf.local.toml", "lies inside a control area"),
                ("nested-git", "sub/.git/config", "lies inside a control area"),
                ("nested-working", "docs/store/.working/imported/x.md", "lies inside a control area"),
                ("nested-pointer-case", "docs/.OPF.TOML", "lies inside a control area"),
                # The store-root control dirs are refused at ANY depth too: the store's own
                # exclusions anchor at the STORE root, and a store root need not be the product
                # root, so docs/.aiqt/ is some potential store's control dir.
                ("nested-store-area", "docs/.aiqt/x", "lies inside a control area"),
                ("nested-store-area-case", "sub/.AIQT/config", "lies inside a control area"),
                # The alias spellings git itself treats as .git (NTFS trailing dot and space, the
                # 8.3 short name, an alternate-data-stream suffix, HFS+-ignorable code points),
                # each refused, and the SAME forms of the store control names and pointer files.
                ("git-trailing-dot", ".git./x", "lies inside a control area"),
                ("git-trailing-space", ".git /x", "lies inside a control area"),
                ("git-shortname", "GIT~1/x", "lies inside a control area"),
                ("git-stream", ".git::" + chr(0x24) + "INDEX_ALLOCATION/x",
                 "lies inside a control area"),
                ("git-zwnj", ".g\u200cit/x", "lies inside a control area"),
                ("git-bom", ".git\ufeff/x", "lies inside a control area"),
                ("nested-git-trailing-dot", "sub/.GIT./config", "lies inside a control area"),
                ("store-area-trailing-dot", ".aiqt./x", "lies inside a control area"),
                ("store-area-trailing-space", ".AIQT /x", "lies inside a control area"),
                ("store-area-shortname", "AIQT~1/x", "lies inside a control area"),
                ("store-area-stream", ".aiqt::" + chr(0x24) + "INDEX_ALLOCATION/x",
                 "lies inside a control area"),
                ("store-area-zwj", ".ai\u200dqt/x", "lies inside a control area"),
                ("working-trailing-dot", ".working./x", "lies inside a control area"),
                ("working-shortname", "WORKIN~1/x", "lies inside a control area"),
                ("working-bom", ".working\ufeff/x", "lies inside a control area"),
                ("pointer-trailing-dot", ".opf.toml.", "lies inside a control area"),
                ("pointer-trailing-space", ".opf.toml ", "lies inside a control area"),
                ("pointer-shortname", "OPF~1.TOM", "lies inside a control area"),
                ("pointer-shortname-later", "opf~4.tom", "lies inside a control area"),
                ("pointer-stream", ".opf.toml::" + chr(0x24) + "DATA",
                 "lies inside a control area"),
                ("local-pointer-shortname", "OPFLOC~1.TOM", "lies inside a control area"),
                ("local-pointer-trailing-dot", "docs/.opf.local.toml.",
                 "lies inside a control area")):
            got = parse_batch(_edited('path = "notes/old.md"', 'path = "{}"'.format(bad)))
            check("source-path-" + name + "-refused", got.status == INVALID
                  and any(text in f for f in got.findings))
        for name, good in (("github-root", ".github/workflows/ci.yml"), ("git-substring", "my.git/x"),
                           ("gitlike-dir", "git/x"),
                           # Just OUTSIDE each alias form: no short-name tilde or digits, a stem
                           # that is not a control stem, a four-character extension no 8.3 name
                           # carries, a trailing dot on a NON-control name, and prefixed spellings
                           # that fold to nothing in the control set.
                           ("gitx-shortname", "gitx~1/x"),
                           ("git-tilde-no-digit", "git~/x"),
                           ("toml-long-ext", "opf~1.toml"),
                           ("dot-tail-noncontrol", "notes/old.md."),
                           ("working-prefix", ".workingx/x"),
                           ("store-area-prefix", ".aiqtx/x")):
            got = parse_batch(_edited('path = "notes/old.md"', 'path = "{}"'.format(good)))
            check("source-path-" + name + "-valid", got.status == VALID)
        # NTFS numeric tails past one digit cut the stem (8.3 keeps eight characters before the
        # extension), so WORKI~10, WORK~100 and OPFLO~10.TOM alias .working and .opf.local.toml;
        # worki~1 is the just-outside positive (a one-digit tail keeps the full six-character stem,
        # WORKIN~1, so this five-character spelling aliases nothing), red when the cut-stem arms
        # admit a shorter run than their cut implies.
        for name, bad in (("working-shortname-two-digit", "WORKI~10/x"),
                          ("working-shortname-three-digit", "WORK~100/x"),
                          ("local-pointer-shortname-two-digit", "OPFLO~10.TOM")):
            got = parse_batch(_edited('path = "notes/old.md"', 'path = "{}"'.format(bad)))
            check("source-path-" + name + "-refused",
                  _refuses(got, INVALID, "lies inside a control area"))
        got = parse_batch(_edited('path = "notes/old.md"', 'path = "worki~1/x"'))
        check("source-path-working-short-prefix-one-digit-valid", got.status == VALID)
        # The REMAINING tail lengths these spelled vectors reach (a d-digit tail cuts the stem to
        # 7 - d characters): .working at 4, 5 and 6, .git at 5, .aiqt at 4, and the pointer names
        # at 5 and 6 (an op~/o~ spelling matches BOTH pointer arms, so a pointer vector pins the
        # pair, never one name's arm alone), each beside its ADJACENT acceptance: the same cut
        # stem with one digit fewer matches no arm and stays a plain product path. The arms no
        # spelled vector reaches or isolates (.git at 6, .aiqt at 5 and 6, and .opf.local.toml's
        # arms beyond OPFLO~, unreached or shared with another name's arm) are pinned ONLY by the
        # literal arm table below. Narrowing range(2, 7) to (2, 4), (2, 5) or (2, 6), skipping
        # the cut arms of a six-character stem, or a cut arm admitting a shorter run than its cut
        # implies, turns one of these red.
        for name, bad in (("working-shortname-four-digit", "WOR~1000/x"),
                          ("working-shortname-five-digit", "WO~10000/x"),
                          ("working-shortname-six-digit", "W~100000/x"),
                          ("git-shortname-five-digit", "GI~12345/x"),
                          ("store-area-shortname-four-digit", "AIQ~1234/x"),
                          ("pointer-shortname-five-digit", "OP~10000.TOM"),
                          ("pointer-shortname-six-digit", "O~100000.TOM")):
            got = parse_batch(_edited('path = "notes/old.md"', 'path = "{}"'.format(bad)))
            check("source-path-" + name + "-refused",
                  _refuses(got, INVALID, "lies inside a control area"))
        for name, good in (("working-short-prefix-three-digit", "wor~100/x"),
                           ("working-short-prefix-four-digit", "wo~1000/x"),
                           ("working-short-prefix-five-digit", "w~10000/x"),
                           ("git-short-prefix-four-digit", "gi~1234/x"),
                           ("store-area-short-prefix-three-digit", "aiq~123/x"),
                           ("pointer-short-prefix-four-digit", "op~1000.tom"),
                           ("pointer-short-prefix-five-digit", "o~10000.tom")):
            got = parse_batch(_edited('path = "notes/old.md"', 'path = "{}"'.format(good)))
            check("source-path-" + name + "-valid", got.status == VALID)
        # The 8.3 arm table itself, pinned LITERALLY for every control name and every digit run,
        # spelled out as data rather than re-derived at run time: the full-stem any-run arm, each
        # cut stem with its exact run length, and the cut extension where the name carries one.
        # A dropped run, a changed cut or a lost extension turns this red.
        expected_short = {
            ".git": "(?:git~[0-9]+|gi~[0-9]{5}|g~[0-9]{6})",
            ".aiqt": "(?:aiqt~[0-9]+|aiq~[0-9]{4}|ai~[0-9]{5}|a~[0-9]{6})",
            ".working": "(?:workin~[0-9]+|worki~[0-9]{2}|work~[0-9]{3}|wor~[0-9]{4}|wo~[0-9]{5}"
                        "|w~[0-9]{6})",
            ".opf.toml": "(?:opf~[0-9]+|op~[0-9]{5}|o~[0-9]{6})\\.tom",
            ".opf.local.toml": "(?:opfloc~[0-9]+|opflo~[0-9]{2}|opfl~[0-9]{3}|opf~[0-9]{4}"
                               "|op~[0-9]{5}|o~[0-9]{6})\\.tom",
        }
        check("dos-short-patterns-pinned", set(_CONTROL_COMPONENTS) == set(expected_short)
              and all(_dos_short_pattern(n) == expected_short[n] for n in expected_short))
        # Every declared HFS+-ignorable code point, enumerated INDEPENDENTLY of _HFS_IGNORABLE (the
        # expected set is never re-derived from the implementation): U+200C..U+200F, U+202A..U+202E,
        # U+206A..U+206F and U+FEFF, each dropped by an HFS+ name lookup, so .g<cp>it/x names .git.
        # The raw code point rides each vector: where the emitter escapes it the document is also
        # non-canonical, but the control-area finding is asserted by name, so removing any ONE
        # member from the fold loses that finding and turns its enumerated vector red, together
        # with every NAMED vector riding the same code point (U+FEFF also turns git-bom and
        # working-bom red, U+200C git-zwnj, U+200D store-area-zwj; the other thirteen fold
        # members fail alone).
        hfs_declared = ([0xFEFF] + list(range(0x200C, 0x2010))
                        + list(range(0x202A, 0x202F)) + list(range(0x206A, 0x2070)))
        for cp in hfs_declared:
            tag = "U+{:04X}".format(cp)
            got = parse_batch(_edited('path = "notes/old.md"',
                                      'path = ".g' + chr(cp) + 'it/x"'))
            check("source-path-hfs-ignorable-" + tag + "-refused", got.status == INVALID
                  and any("lies inside a control area" in f for f in got.findings))
        got = parse_batch(_edited('path = "notes/old.md"\n', ''))
        check("missing-source-path-refused", _refuses(got, INVALID, "missing required field: source.path"))
        got = parse_batch(_edited('\n[source]\npath = "notes/old.md"\n', '\n'))
        check("missing-source-table-refused", _refuses(got, INVALID, "missing required table"))
        head = _FIXTURE.decode("utf-8").split("\n[[record]]", 1)[0].encode("utf-8")
        check("empty-batch-refused", _refuses(parse_batch(head), INVALID, "holds no record and no worklog row"))

        # Validation through validate_imported_record (IS-0): the missingness contract and
        # the import stays load-bearing in the parse (red when the validation call is emptied).
        unrec_line = _FIXTURE.decode("utf-8").splitlines()[11]
        got = parse_batch(_edited(unrec_line + "\n", ""))
        check("validator-missing-unrecorded-refused", got.status == INVALID
              and any("has no unrecorded row" in f for f in got.findings))
        got = parse_batch(_edited('status = "done"\n', 'proposed_from = "open"\nstatus = "done"\n'))
        check("validator-proposed-from-refused", got.status == INVALID
              and any("never carries proposed_from" in f for f in got.findings))
        # Collection scope pinned (the module docstring's three early stops): a source finding that
        # is NOT a missing or unusable path, a caller-supplied measurement in [source] included,
        # leaves row building intact, so the rows' own validator findings ride beside it, while an
        # UNUSABLE path builds and validates no row and staging's writer-authored refusal still
        # fires (red when row building is skipped on any source finding, or run on a path finding).
        got = parse_batch(_edited('path = "notes/old.md"\n', 'path = "notes/old.md"\nsize = 5\n')
                          .replace(b'status = "done"\n',
                                   b'proposed_from = "open"\nstatus = "done"\n', 1))
        check("source-measurement-leaves-row-building-intact", got.status == INVALID
              and any("caller-supplied measurement 'size'" in f for f in got.findings)
              and any("never carries proposed_from" in f for f in got.findings))
        # "Builds no row" is asserted AT THE SEAMS, not only by the absent validator finding:
        # _resolve_links is the first step of every row build and validate_imported_record the
        # one validation entry, so a parse that reaches either under an unusable source path
        # turns this red even where its findings are dropped (red when rows are built and only
        # validation is skipped, a gap a finding-only assertion cannot see).
        bad_src = _edited('path = "notes/old.md"', 'path = "../x"')
        bad_src = bad_src.replace(b'key = "r1"\ntype = "backlog_item"\n',
                                  b'key = "r1"\ntype = "backlog_item"\nid = "XX-1"\n', 1)
        bad_src = bad_src.replace(b'status = "done"\n',
                                  b'proposed_from = "open"\nstatus = "done"\n', 1)
        seam_calls = {"build": 0, "validate": 0}
        real_resolve, real_validate = _resolve_links, schema.validate_imported_record

        def _counting_resolve(*args, **kwargs):
            seam_calls["build"] += 1
            return real_resolve(*args, **kwargs)

        def _counting_validate(*args, **kwargs):
            seam_calls["validate"] += 1
            return real_validate(*args, **kwargs)

        globals()["_resolve_links"] = _counting_resolve
        schema.validate_imported_record = _counting_validate
        try:
            got = parse_batch(bad_src)
        finally:
            globals()["_resolve_links"] = real_resolve
            schema.validate_imported_record = real_validate
        check("unusable-source-path-builds-no-row", got.status == INVALID
              and seam_calls == {"build": 0, "validate": 0}
              and any("not a canonical product-relative file path" in f for f in got.findings)
              and any("writer-authored member 'id'" in f for f in got.findings)
              and not any("proposed_from" in f for f in got.findings))

        # A supplied historical timestamp is accepted (the placeholder import clock is the latest
        # instant, so the real bound binds at write time, where the writer re-validates).
        timed = _edited('unrecorded = [{field = "created_at", reason = "not_recorded_in_source"}, ', "unrecorded = [")
        timed = timed.replace(b'links = [{key', b'created_at = "2003-04-05T06:07:08Z"\nlinks = [{key', 1)
        check("historical-timestamp-valid", parse_batch(timed).status == VALID)

        # Provenance content rows ride the batch: span, history and unparsed move into the built
        # import table, and the validator judges them there.
        hist_line = 'history = [{field = "title", value = "old"}]\n'
        histed = _edited('links = [{key', hist_line + "links = [{key")
        got = parse_batch(histed)
        check("provenance-history-into-import", got.status == VALID
              and got.batch.rows[0].record["import"]["history"] == [{"field": "title", "value": "old"}])
        got = parse_batch(histed.replace(b"title", b"nosuch", 1))
        check("provenance-history-bad-field-refused", got.status == INVALID
              and any("names no field of the type's schema" in f for f in got.findings))
        spanned_bad = _edited('status = "done"\n', 'span = [5, 2]\nstatus = "done"\n')
        got = parse_batch(spanned_bad)
        check("provenance-span-bad-refused", _refuses(got, INVALID, "import.span"))
        spanned = spanned_bad.replace(b"[5, 2]", b"[2, 5]", 1)
        check("provenance-span-valid", parse_batch(spanned).status == VALID)

        # Placeholder numbering runs per namespace in batch order and is never an allocation.
        second = "\n[[worklog]]" + _FIXTURE.decode("utf-8").rsplit("[[worklog]]", 1)[1]
        second = second.replace("w1", "w2").replace("the old item note", "the second note")
        two = _FIXTURE + second.encode("utf-8")
        got = parse_batch(two)
        check("placeholder-numbering-per-namespace", got.status == VALID
              and [r.placeholder_id for r in got.batch.rows]
              == ["imported:{}-01".format(bi), "imported:{}-01".format(wl),
                  "imported:{}-02".format(wl)])

        # The aliasing rule holds at CLASS WIDTH: a block row's `scopes` is the only other
        # record-id-bearing row member (the enumeration sits on _refuse_minted_scopes and in the
        # module docstring). A verbatim scope naming a real imported id from an earlier run is never
        # refused and can never collide with a placeholder, while a scope spelling a minted
        # placeholder is the genuine alias, refused by name and ALONE (validation sees the mask, so
        # no second shape finding rides along).
        bk = store.BASELINE_TYPES["block"]
        block_row = ('\n[[record]]\nkey = "b1"\ntype = "block"\n'
                     'scopes = ["imported:' + bi + '-1"]\nstatus = "active"\ntitle = "Old block"\n'
                     'unrecorded = [{field = "created_at", reason = "not_recorded_in_source"}, '
                     '{field = "updated_at", reason = "not_recorded_in_source"}]\n')
        blocked = _edited("\n[[worklog]]", block_row + "\n[[worklog]]")
        got = parse_batch(blocked)
        check("scopes-existing-id-valid", got.status == VALID
              and got.batch.rows[1].record["scopes"] == ["imported:" + bi + "-1"]
              and got.batch.rows[1].placeholder_id == "imported:" + bk + "-01"
              and "imported:" + bi + "-1" not in {r.placeholder_id for r in got.batch.rows})
        aliased = blocked.decode("utf-8").replace(
            'scopes = ["imported:' + bi + '-1"]',
            'scopes = ["imported:' + bi + '-01"]', 1).encode("utf-8")
        got = parse_batch(aliased)
        check("scopes-minted-placeholder-refused",
              _refuses(got, INVALID, "spells a placeholder this batch itself mints")
              and len(got.findings) == 1 and "scopes[0]" in got.findings[0]
              and not any("not a record id" in f for f in got.findings))

        # The placeholder contract is POSITIONAL (the writer's future obligation, the spec 8.8
        # allocation seam): placeholders are replaced only at the generated reference positions
        # located structurally from the parsed rows (the row's own id and each key-resolved
        # links[].id), and the spelling is refused only at record-id positions, so
        # placeholder-LOOKING text in a title, a history row's value, a refs locator or a refs
        # note stays VALID, rides the batch BYTE-IDENTICAL beside the real reference positions
        # that carry the placeholder, and is never re-spelled (red when a spelling scan refuses
        # or rewrites preserved text).
        bi_ph, wl_ph = "imported:{}-01".format(bi), "imported:{}-01".format(wl)
        titled = _edited('title = "Old backlog item"', 'title = "{}"'.format(bi_ph))
        got = parse_batch(titled)
        check("placeholder-text-title-preserved", got.status == VALID
              and got.batch.canonical == titled
              and got.batch.rows[0].record["title"] == bi_ph
              and got.batch.rows[0].record["id"] == bi_ph
              and got.batch.rows[0].record["links"] == [{"id": wl_ph, "rel": "relates"}])
        histed_ph = _edited("links = [{key",
                            'history = [{field = "title", value = "' + bi_ph + '"}]\nlinks = [{key')
        got = parse_batch(histed_ph)
        check("placeholder-text-history-value-preserved", got.status == VALID
              and got.batch.canonical == histed_ph
              and got.batch.rows[0].record["import"]["history"]
              == [{"field": "title", "value": bi_ph}])
        refed = _edited('status = "done"\n',
                        'refs = [{kind = "path", locator = "' + bi_ph + '", note = "' + wl_ph
                        + '"}]\nstatus = "done"\n')
        got = parse_batch(refed)
        check("placeholder-text-refs-preserved", got.status == VALID
              and got.batch.canonical == refed
              and got.batch.rows[0].record["refs"]
              == [{"kind": "path", "locator": bi_ph, "note": wl_ph}])
        # An `unparsed` entry carrying placeholder-LOOKING text rides the batch the same way:
        # VALID, byte-identical, and moved into the built import table verbatim. The links array
        # here carries TWO key-resolved links with a verbatim imported id INTERLEAVED between
        # them (a second worklog row backs the second key), so the masked-copy assertion below
        # discriminates a mask applied at every links position from one applied at links[0] only.
        masked_text = _FIXTURE.decode("utf-8").replace(
            'links = [{key = "w1", rel = "relates"}]',
            'history = [{field = "title", value = "' + bi_ph + '"}]\n'
            'links = [{key = "w1", rel = "relates"}, {id = "imported:' + bi + '-9", '
            'rel = "relates"}, {key = "w2", rel = "relates"}]\n'
            'refs = [{kind = "path", locator = "' + bi_ph + '", note = "' + wl_ph + '"}]', 1)
        masked_text = masked_text.replace(
            'title = "Old backlog item"\n',
            'title = "' + bi_ph + '"\nunparsed = ["' + bi_ph + '"]\n', 1)
        masked_text = masked_text + ("\n[[worklog]]"
                                     + _FIXTURE.decode("utf-8").rsplit("[[worklog]]", 1)[1]
                                     ).replace("w1", "w2").replace("the old item note",
                                                                   "the second note")
        masked_doc = masked_text.encode("utf-8")
        got = parse_batch(masked_doc)
        # mrow survives a refused parse as None, so a masking mutant that turns this fixture
        # INVALID grades the vectors below red by name (exit 1), never the exit-2 backstop.
        mrow = got.batch.rows[0] if got.batch is not None else None
        wl2_ph = "imported:{}-02".format(wl)
        check("placeholder-text-unparsed-preserved", mrow is not None and got.status == VALID
              and got.batch.canonical == masked_doc
              and mrow.record["import"]["unparsed"] == [bi_ph])
        # The SEPARATE validation copy, asserted directly: _masked_for_validation re-spells minted
        # placeholders at EXACTLY the record-id-bearing positions (here the record's own id and
        # the key-resolved links[0].id AND links[2].id, every such position, never only the
        # first) and changes nothing else, so the copy equals the built record with only those
        # three members re-spelled, the verbatim links[1].id, the placeholder-LOOKING title,
        # history value, refs locator and note and unparsed entry preserved verbatim, and the
        # built record itself untouched (red when a spelling scan re-spells preserved text
        # anywhere in the copy, when only links[0] is masked, or when the helper writes through
        # to the stored record).
        # The three placeholders this fixture mints, spelled literally so a refused parse can
        # never crash the derivation (fail-closed to a red check instead).
        minted_here = frozenset({bi_ph, wl_ph, wl2_ph})
        before = None if mrow is None else copy.deepcopy(mrow.record)
        masked = None if mrow is None else _masked_for_validation(mrow.record, minted_here)
        expected = None if mrow is None else copy.deepcopy(before)
        if expected is not None:
            expected["id"] = _validation_mask(bi_ph)
            expected["links"][0]["id"] = _validation_mask(wl_ph)
            expected["links"][2]["id"] = _validation_mask(wl2_ph)
        check("masked-copy-positional-only", mrow is not None and masked == expected
              and masked is not mrow.record and mrow.record == before
              and masked["links"][1] == {"id": "imported:" + bi + "-9", "rel": "relates"}
              and masked["title"] == bi_ph
              and masked["import"]["history"] == [{"field": "title", "value": bi_ph}]
              and masked["refs"] == [{"kind": "path", "locator": bi_ph, "note": wl_ph}]
              and masked["import"]["unparsed"] == [bi_ph])
        # The THIRD record-id-bearing position, `scopes`, asserted by a direct call (a parsed
        # batch refuses a minted scopes entry before validation, so the arm needs a synthetic
        # record), a minted entry sitting BEFORE and AFTER the preserved one, so a mask applied
        # at scopes[0] alone cannot pass: every minted entry is re-spelled to its mask, the
        # verbatim entry naming an earlier run's id is preserved in place, and the synthetic
        # record is untouched.
        bk_ph = "imported:{}-01".format(bk)
        synthetic = {"id": bk_ph, "type": "block",
                     "scopes": [bk_ph, "imported:" + bi + "-7", bk_ph]}
        masked_syn = _masked_for_validation(synthetic, frozenset({bk_ph}))
        check("masked-copy-scopes-position",
              masked_syn == {"id": _validation_mask(bk_ph), "type": "block",
                             "scopes": [_validation_mask(bk_ph), "imported:" + bi + "-7",
                                        _validation_mask(bk_ph)]}
              and synthetic == {"id": bk_ph, "type": "block",
                                "scopes": [bk_ph, "imported:" + bi + "-7", bk_ph]})
        # A returned validator finding labels its row by the PLACEHOLDER spelling, never by the
        # well-formed mask it was judged under: the mask is exactly a spelling a real store
        # record could carry (red when the re-label is dropped).
        got = parse_batch(_edited('{key = "w1", rel = "relates"}',
                                  '{id = "imported:' + bi + '-02", rel = "relates"}'))
        check("finding-labels-row-by-placeholder", got.status == INVALID
              and any("imported record '" + bi_ph + "'" in f for f in got.findings)
              and not any("imported record 'imported:" + bi + "-1'" in f
                          for f in got.findings))

        # A value outside the v1 grammar has no canonical form: refused, never re-spelled.
        got = parse_batch(_edited('status = "done"\n', 'num = 1.5\nstatus = "done"\n'))
        check("float-value-refused", got.status == INVALID
              and any("no canonical batch form" in f for f in got.findings))
        got = parse_batch(_edited('status = "done"\n', 'stamp = 2020-01-02T03:04:05Z\nstatus = "done"\n'))
        check("datetime-value-refused", got.status == INVALID
              and any("no canonical batch form" in f for f in got.findings))

        # The canonical depth bound refuses fail-closed past MAX_CANON_DEPTH: the ITERATIVE preflight
        # returns the one depth finding before any copy, row validation or emission.
        deep_value = "{d = " * (MAX_CANON_DEPTH + 1) + "1" + "}" * (MAX_CANON_DEPTH + 1)
        got = parse_batch(_edited('key = "r1"\ntype = "backlog_item"\n',
                                  'key = "r1"\ntype = "backlog_item"\nd = ' + deep_value + '\n'))
        check("depth-over-bound-cannot", got.status == CANNOT_EVALUATE and got.batch is None
              and len(got.findings) == 1
              and any("deeper than {} levels".format(MAX_CANON_DEPTH) in f for f in got.findings))
        # Both ends of the bound: MAX_CANON_DEPTH - 1 nested containers put the innermost SCALAR at
        # depth MAX_CANON_DEPTH exactly (inside: the unknown row key is the only finding), and one
        # more container pushes that scalar just past it (refused, as the pinned emitter graded it).
        inside_value = "{d = " * (MAX_CANON_DEPTH - 1) + "1" + "}" * (MAX_CANON_DEPTH - 1)
        got = parse_batch(_edited('key = "r1"\ntype = "backlog_item"\n',
                                  'key = "r1"\ntype = "backlog_item"\nd = ' + inside_value + '\n'))
        check("depth-inside-bound-not-depth-refused", got.status == INVALID
              and not any("deeper than" in f for f in got.findings))
        at_value = "{d = " * MAX_CANON_DEPTH + "1" + "}" * MAX_CANON_DEPTH
        got = parse_batch(_edited('key = "r1"\ntype = "backlog_item"\n',
                                  'key = "r1"\ntype = "backlog_item"\nd = ' + at_value + '\n'))
        # Reported ALONE pins the PREFLIGHT bound exactly: were the preflight lenient by one, this
        # row would build and the validator's unknown-key finding would ride beside the emitter
        # backstop's depth refusal, so the single-finding assertion turns red on that revert.
        check("depth-scalar-past-bound-cannot", got.status == CANNOT_EVALUATE and got.batch is None
              and len(got.findings) == 1
              and any("deeper than" in f for f in got.findings))
        # The ITERATIVE preflight refuses a dotted-key structure deep enough to exhaust recursive
        # copying, in ordinary content and in provenance content alike, BEFORE any copy or recursion:
        # a structured refusal, never an exception (on a reverted preflight these raise RecursionError
        # out of deepcopy, which this harness fails closed to exit 2). Each key has 900 parts, past
        # MAX_CANON_DEPTH yet below the key-part cap tomllib applies from CPython 3.14.8 on (a key of
        # more than 1000 parts is refused), so the preflight finding is asserted on every interpreter:
        # the vectors run under _StKeyPartCap, which emulates that cap where the interpreter lacks it.
        with store._StKeyPartCap():
            for name, extra in (("content", "d." * 899 + "leaf = 1\n"),
                                ("provenance", "history." + "d." * 898 + "leaf = 1\n")):
                got = parse_batch(_edited('type = "backlog_item"\n', 'type = "backlog_item"\n' + extra))
                check("deep-dotted-" + name + "-cannot", got.status == CANNOT_EVALUATE
                      and got.batch is None and any("deeper than" in f for f in got.findings))
        # A key past that cap is the PARSER's refusal, never the preflight's: under _StKeyPartCap a
        # 1001-part key raises RecursionError (the class CPython 3.14.8 raises) or MemoryError (a
        # many-part key under a memory limit) inside tomllib, and parse_batch grades either one as
        # unparseable TOML: CANNOT-EVALUATE, no batch and no digest. Each vector also asserts that its
        # injection fired (once in parse_batch, once in batch_digest), so a disengaged cap fails it.
        long_key = _edited('type = "backlog_item"\n',
                           'type = "backlog_item"\n' + "d." * 1000 + "leaf = 1\n")
        for name, kind in (("recursion", RecursionError), ("memory", MemoryError)):
            fired, got, digest = _capped_parse(long_key, kind)
            check("long-key-capped-" + name + "-cannot", fired == 2 and got is not None
                  and _refuses(got, CANNOT_EVALUATE, "not parseable") and digest is None)
        # tomllib's own recursion limit (nested inline arrays) is caught and graded, never an escape.
        brackets = b"x = " + b"[" * 20000 + b"]" * 20000 + b"\n"
        check("toml-recursion-cannot", _refuses(parse_batch(brackets), CANNOT_EVALUATE, "not parseable"))

        # TOML integers are signed 64-bit: both ends of the range parse (INT64_MAX inside a valid span;
        # INT64_MIN under an unknown key, so the only finding is the key's, never a range refusal), one
        # past either end is a structured range refusal, an over-long decimal literal is refused at
        # parse time, and a huge non-decimal literal is refused before any emission or formatting.
        # Every boundary vector is a LITERAL digit string, never spelled from the module's own
        # constants, and the constants are pinned to TOML's bounds, so a drifted INT64_MIN or
        # INT64_MAX cannot move the vectors with it (each end's cases are derived for that end).
        check("int64-bounds-are-tomls", INT64_MAX == 9223372036854775807
              and INT64_MIN == -9223372036854775808)
        got = parse_batch(_edited('status = "done"\n',
                                  'span = [0, 9223372036854775807]\nstatus = "done"\n'))
        check("int-at-max-valid", got.status == VALID)
        got = parse_batch(_edited('status = "done"\n',
                                  'span = [0, 9223372036854775808]\nstatus = "done"\n'))
        check("int-over-max-refused", _refuses(got, INVALID, "signed 64-bit"))
        got = parse_batch(_edited('status = "done"\n', 'n = -9223372036854775808\nstatus = "done"\n'))
        check("int-at-min-not-range-refused", got.status == INVALID
              and not any("signed 64-bit" in f for f in got.findings))
        got = parse_batch(_edited('status = "done"\n', 'n = -9223372036854775809\nstatus = "done"\n'))
        check("int-under-min-refused", _refuses(got, INVALID, "signed 64-bit"))
        got = parse_batch(_edited('status = "done"\n', "n = " + "9" * 5000 + '\nstatus = "done"\n'))
        check("int-overlong-decimal-cannot", _refuses(got, CANNOT_EVALUATE, "not parseable"))
        # Both bounds JUST INSIDE: INT64_MAX - 1 in a valid span stays VALID, and INT64_MIN + 1 under
        # an unknown key draws no range finding (the at-bound and just-outside vectors sit above, so
        # each end carries inside, at and outside cases, each derived for its own end).
        got = parse_batch(_edited('status = "done"\n',
                                  'span = [0, 9223372036854775806]\nstatus = "done"\n'))
        check("int-just-inside-max-valid", got.status == VALID)
        got = parse_batch(_edited('status = "done"\n', 'n = -9223372036854775807\nstatus = "done"\n'))
        check("int-just-inside-min-not-range-refused", got.status == INVALID
              and not any("signed 64-bit" in f for f in got.findings))
        # The PREFLIGHT ITSELF is pinned apart from the emitter backstop: a preflight refusal is
        # reported ALONE, while with the preflight's int check removed, or limited to depth 1, or
        # its depth bound lenient by one, the same vectors draw staging, validator or canonicality
        # findings beside the backstop's, so each such revert turns one of these red. The members
        # cover every refusal seam a huge int can reach (key and type at staging, status through the
        # validator, a history row at depth 2, source.path through the source check).
        big = str(INT64_MAX + 1)
        for name, needle, repl in (
                ("key", 'key = "r1"\n', "key = " + big + "\n"),
                ("type", 'type = "backlog_item"\n', "type = " + big + "\n"),
                ("status", 'status = "done"\n', "status = " + big + "\n"),
                ("history", "links = [{key", "history = [" + big + "]\nlinks = [{key"),
                ("source-path", 'path = "notes/old.md"\n', "path = " + str(INT64_MIN - 1) + "\n")):
            got = parse_batch(_edited(needle, repl))
            check("preflight-int-" + name + "-reported-alone",
                  got.status == INVALID and got.batch is None and len(got.findings) == 1
                  and "signed 64-bit" in got.findings[0])
        got = parse_batch(_edited('status = "done"\n', "n = 0x" + "f" * 4000 + '\nstatus = "done"\n'))
        check("int-huge-hex-refused", _refuses(got, INVALID, "signed 64-bit"))

        # The emitter refuses a non-document directly, never emitting a best effort.
        try:
            canonical_batch_bytes("x")
            direct = False
        except _CanonError as exc:
            direct = exc.status == INVALID
        check("canonical-direct-gate", direct)
        # The emitter backstop refuses an out-of-range integer for a direct caller too, by name.
        try:
            canonical_batch_bytes({"schema": BATCH_SCHEMA, "source": {"path": "notes/old.md"},
                                   "record": [{"key": "r1", "n": INT64_MAX + 1}]})
            direct_int = False
        except _CanonError as exc:
            direct_int = exc.status == INVALID and "signed 64-bit" in str(exc)
        check("canonical-direct-int-gate", direct_int)

        # The emitter rules are pinned BYTE-EXACT by the second fixture: the backslash-u escape set and
        # its upper-case hex, the boolean spellings, and the quoted-key rule, both at a row key and
        # inside an inline table (red on an emitter re-spelling of any of them).
        check("emitter-rules-pinned",
              canonical_batch_bytes(tomllib.loads(_EMITTER_FIXTURE.decode("utf-8"))) == _EMITTER_FIXTURE)
        got = parse_batch(_EMITTER_FIXTURE)
        check("emitter-fixture-canonical-on-acceptance-path", got.status == INVALID
              and not any("not canonical" in f for f in got.findings)
              and any("unknown key" in f for f in got.findings))

        # Raw invisible characters: a raw C1 control and a raw bidirectional override are refused
        # non-canonical (their canonical spelling is the escape), and the escaped spelling is the one
        # accepted form.
        got = parse_batch(_edited('title = "Old backlog item"', 'title = "Old\u009b backlog item"'))
        check("raw-c1-refused", _refuses(got, INVALID, "not canonical"))
        got = parse_batch(_edited('title = "Old backlog item"', 'title = "Old\u202e backlog item"'))
        check("raw-bidi-refused", _refuses(got, INVALID, "not canonical"))
        got = parse_batch(_edited('title = "Old backlog item"', 'title = "Old\\u009B backlog item"'))
        check("escaped-c1-valid", got.status == VALID)

        # The REQUIRED escape set, enumerated INDEPENDENTLY of _EXTRA_ESCAPED (the expected set is
        # never re-derived from the implementation, so a shrunken emitter table turns these red):
        # DEL, every C1 control, LS, PS, ALM, LRM, RLM, the embeddings U+202A..U+202E, the isolates
        # U+2066..U+2069, and the zero-width set U+200B, U+200C, U+200D and U+FEFF (DECIDED:
        # escaped, not merely disclosed). Three pins per code point: the emitter's exact upper-case
        # backslash-u spelling, the raw character refused (for DEL alone, TOML itself forbids the
        # raw form in a basic string, so tomllib refuses it unparseable; fail-closed either way),
        # and the escaped spelling as the one accepted form.
        required_escapes = ([0x7F, 0x2028, 0x2029, 0x061C, 0x200E, 0x200F,
                             0x200B, 0x200C, 0x200D, 0xFEFF]
                            + list(range(0x80, 0xA0))       # every C1 control
                            + list(range(0x202A, 0x202F))   # LRE, RLE, PDF, LRO, RLO
                            + list(range(0x2066, 0x206A)))  # LRI, RLI, FSI, PDI
        for cp in required_escapes:
            tag = "U+{:04X}".format(cp)
            check("escape-exact-" + tag,
                  _canon_string("a" + chr(cp)) == '"a' + "\\u" + "{:04X}".format(cp) + '"')
            raw = parse_batch(_edited('title = "Old backlog item"',
                                      'title = "Old' + chr(cp) + ' item"'))
            if cp == 0x7F:
                check("escape-raw-" + tag + "-refused",
                      _refuses(raw, CANNOT_EVALUATE, "not parseable"))
            else:
                check("escape-raw-" + tag + "-refused", _refuses(raw, INVALID, "not canonical"))
            if cp in (0x85, 0x2028, 0x2029):
                # NEL, LS and PS are splitlines boundaries, which the single-line `title` refuses
                # for its own reason, so their escaped-acceptance pin rides in `summary`, a plain
                # (multi-line-capable) string field of a full-envelope record.
                esc = parse_batch(_edited('title = "Old backlog item"',
                                          'summary = "Old\\u' + "{:04X}".format(cp)
                                          + ' item"\ntitle = "Old backlog item"'))
            else:
                esc = parse_batch(_edited('title = "Old backlog item"',
                                          'title = "Old\\u' + "{:04X}".format(cp) + ' item"'))
            check("escape-escaped-" + tag + "-valid", esc.status == VALID)

    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never an uncaught exit-1 escape
        print("OPF-IMPORT-BATCH SELF-TEST: harness error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return 2
    failed = [name for name, ok in results if not ok]
    for name in failed:
        print("FAIL: " + name, file=sys.stderr)
    print("OPF-IMPORT-BATCH SELF-TEST: {} checks, {} failed".format(len(results), len(failed)))
    return 1 if failed else 0


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    print("usage: _opf_import_batch.py --self-test (a library module: parse_batch(data), "
          "batch_digest(data))", file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
