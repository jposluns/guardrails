#!/usr/bin/env python3
"""Generate the .claude/rules/ read tree from .aiqt/core/rules/ sources (the source-and-adapter machinery).

Each source is a Markdown rule with a minimal YAML `---` frontmatter carrying its classification; its read
path is DERIVED from that frontmatter per the two-axis taxonomy (aiqt/ numbered by priority, security/
coded CIA+P). The updater's write root is `.aiqt/core/`; CI runs this in --check so the read tree can never
silently drift (including orphaned generated files with no source). Vendored `external/` trees are untouched.

RULE SOURCE FORMAT (the two-layer split; this step parses and validates it, and moves no text yet):
  A rule source is read from its raw bytes as UTF-8 with no newline translation, and a source holding
  any CR byte is refused (check_clauses reads it the same way, through read_rule_source and
  decode_rule_source, so both tools number lines alike). The split is the one body line that reads
  exactly `## Detail` (column 0, no trailing whitespace) with a blank line directly above it. The text above it is the CORE layer and the text below it is the DETAIL layer. Two
  optional frontmatter keys go with it, on aiqt non-apex and security rules only (the apex never splits):
    detail-trigger: <a short phrase naming the operation>   required when the split exists
    detail-reason:  <one line recording the must-fire review> optional when the split exists
  BODY GRAMMAR (decision D-392-ENUMERATED-BODY-GRAMMAR): this reader models no Markdown. Every body line
  must be one of these shapes, chosen so that a CommonMark renderer can build no block from the body
  other than ATX headings and paragraphs, so its rendered headings are exactly its title line and its
  `## Detail` line by construction:
    - a blank line (empty);
    - the title line, which is the first non-blank body line and the only one: `# ` and non-blank text
      that is printable ASCII (0x20 to 0x7E) holding none of [ ] < > & ~ * _, a backslash or a
      backtick (`(` and `)` are allowed, decision D-392-PLAIN-BODY-UNBLOCK: with no `[` no link or
      image can form);
    - the split line, exactly `## Detail`, at most once;
    - a prose line: printable ASCII (0x20 to 0x7E) holding no `<`, `[`, `]` or backslash; its first
      character is none of a space, a tab, # > - + * = ~ | < [ ! _ and `:`, and it does not start
      with three backticks (a fence) or like an ordered list item (digits, then `.` or `)`, then a
      space or the end of the line). A single leading backtick is allowed (a code span; the live
      corpus starts two lines with one), and so are `>`, `|`, `_` and `&` after the first character.
  FRONTMATTER (decision D-392-FRONTMATTER-TYPED-GRAMMAR): this reader and a YAML reader must see the
  same keys and the same values, so no YAML is modelled: every known key has one declared value type,
  a value is accepted only in a shape of its type, and anything else is refused. The frontmatter is
  split into lines at LF only, as the body is. Each raw line, before any strip, holds no character of
  Unicode category Cc, Cf, Cn, Co, Cs, Zl or Zp (so no tab, U+0085, U+2028, U+2029, U+001C to U+001F,
  U+FFFE or U+FFFF), except ZWNJ and ZWJ (U+200C and U+200D). A line holding only spaces is skipped. Every
  other line is `key: value`: the key at column 0, `:`, at least one space, the value, and optional
  trailing spaces. A comment line (spaces, then `#`; YAML ignores it) is refused, and so is any other
  indented line, except as ADOPTER MODE below allows. The value types, matched against the whole value:
    corpus-id       an id: `[a-z][a-z0-9]{5,}` (a string), `[1-9][0-9]{5,}` (an integer, as YAML reads
                    it), or quoted (below) around `[a-z0-9]{6,}` (a string);
    origin, family  a code: `[a-z]+`, bare or quoted (a string);
    slug            `[a-z0-9]+(-[a-z0-9]+)*` starting with a letter when bare, or quoted (a string);
    tier            exactly `10`, `20`, `30` or `40`, bare (an integer; no leading zero, no quotes);
    apex            exactly `true` or `false`, bare (a boolean);
    facet           a facet code: `[A-Z]{4,5}`, bare or quoted (a string);
    secondary       a flow sequence of facet codes;
    map-*           a flow sequence of mapping ids. A bare id is `[A-Za-z][A-Za-z0-9.&()-]*` with
                    single spaces allowed inside (such as `GOVERN 4.1`, `CM-3(2)`, `I&S-02`), or
                    `[0-9]+(\\.[0-9]+){2,}` (such as `6.4.2`), or the disclosed numeric form below; a
                    quoted id is any `[A-Za-z0-9][A-Za-z0-9.&()-]*` with single inner spaces (a string);
    detail-trigger, detail-reason (the prose keys)
                    FREE TEXT: letters, digits and spaces (Unicode categories L, M, N and Zs), ZWNJ, ZWJ
                    and the punctuation . , ; ( ) ' / - only, starting with a letter (category L) and
                    ending with no space (Zs), bare or quoted (a string);
    pack-id, restates (the worker-pack profile, which gen_worker_pack reads through this parser)
                    pack-id is a slug; restates is a flow sequence of ids (as corpus-id).
  Any other key is refused (exit 2). QUOTED means one matching pair of `"` or `'` around the text, with
  no quote of that kind inside it (so YAML reads a plain string with no escape). A bare string value is
  never a word some YAML reader resolves to a boolean or a null (`y`, `n`, `yes`, `no`, `true`, `false`,
  `on`, `off` or `null` in any case). A flow sequence is `[`, its elements separated by `,` (spaces
  allowed around each), and `]`; `[]` is empty. AGREEMENT BY CONSTRUCTION: no accepted shape holds `#`,
  `:`, `{`, `}`, `[` or `]` inside a scalar, a backslash, a tab or a YAML line break, so YAML reads the
  same lines and keys; a bare string starts with a letter (or is the dotted form `6.4.2`), so no YAML 1.1
  int, float, timestamp, merge or value resolver matches it, and the bool and null words are refused; a
  bare integer is `[1-9][0-9]*` and a bare boolean is `true` or `false`, which YAML reads alike. One gap
  is accepted and disclosed: a bare mapping id that matches `(0|[1-9][0-9]{0,3})\\.[0-9]{0,5}[1-9]` and
  is exactly the shortest decimal text of its float (`repr(float(id)) == id`; the live ids `6.2`, `6.7`,
  `8.1` and `10.2`) is a string here and a float to YAML, and that float prints as the same id. Any other
  float-shaped bare id is refused, such as `6.70`, `06.7` and `0.00001` (whose float prints as `1e-05`);
  quote an id (`["6.7"]`) to make YAML read a string too. The live sources are not edited.
  ADOPTER MODE (the placement gate, check_rule_placement, and conformance C3 through it, for rule files
  in an adopter's .claude/rules/): each CRLF is read as LF before parsing, and a comment line (any
  number of spaces, then `#`) is skipped, as YAML skips it. A comment line may hold a tab after its `#`;
  any other hidden character in it is still refused (YAML reads U+0085, U+2028 and U+2029 as line
  breaks, so the text after one could be a key to YAML). A lone CR (classic Mac line endings) is still
  refused, although YAML reads it as a line break. The pack's own corpus (gen_rules, check_clauses)
  keeps LF only and no comment line.
  ADOPTER NOTE: an adopter rule file that passed the placement gate before this typed grammar may now be
  refused. The refusal names the file and quotes the line or value. The newly refused shapes are:
    - a quoted tier (`tier: "10"`): write it bare (`tier: 10`);
    - a bare slug or corpus-id that starts with a digit (`slug: 1-team`, `corpus-id: 1teamr`): quote it
      (`slug: "1-team"`); a corpus-id of digits only with no leading zero is still accepted bare;
    - lone-CR line endings: save the file with LF or CRLF line endings;
    - a YAML-special word or form as a value, such as `on`, `off`, `yes`, `no`, `y`, `n` or `null` in
      any case where a string is expected, `~`, a leading YAML indicator (`&`, `*`, `!`, `>`, `|`, `?`,
      `-`), a ` #` comment after a value, or a number with a leading zero: quote the value or reword it;
    - a tab outside a comment (after a key's colon, in a value, or before a `#`): use spaces;
    - spaces before a key, including a whole frontmatter block indented alike (YAML reads that as a
      mapping), or no space after a key's colon (`slug:team-review`): write each `key: value` at column 0;
    - a hidden or format character (such as U+200B, U+00AD or U+FEFF) anywhere in the frontmatter,
      including in a comment line: delete it;
    - a mapping id outside the id grammar (`A_1`, `"A.5/1"`), or a bare float id that does not print back
      as itself (`6.70`, `6.0`, `0.00001`): quote a float id (`["6.70"]`) and reword any other id.
  In a renderer that does not read frontmatter, the opening `---` is a thematic break and the frontmatter
  lines are one paragraph, which the closing `---` makes a setext h2; if the last frontmatter line holds
  a `|`, a GFM renderer can read the lines as a table instead. Either way the first text is a frontmatter
  key, a blank frontmatter line can only shorten that heading to the lines after it, and no value holds
  HTML or a bracketed link, so the frontmatter can never add a Detail heading or hide one. A value can
  still render as a GFM autolink (a bare URL, a `www.` address or an email address); that adds no heading.
  Each of these is refused as a malformed source (exit 2, naming the file and, for a line, its number):
    - either key without the split, a missing detail-trigger with it, or a key value that is not a
      non-empty string;
    - a frontmatter line or value refused under FRONTMATTER above;
    - a second `## Detail` line;
    - a `## Detail` line without a blank line directly above it;
    - a body with no title line, and a body line outside the BODY GRAMMAR;
    - anywhere in the body, any other line whose visible text reads `detail` or `details`: the visible
      text is the line with HTML entities decoded, NFKC-normalized, invisible (format) characters,
      leading blockquote and list item markers, HTML tags and link targets dropped, then only its
      letters and digits kept, case-folded (a title or prose line such as `# Details` or `DETAIL.`);
    - an empty detail layer (blank lines only).
  Every body is read in full, with or without the split.
  gen_rules.py           regenerate .claude/rules/{aiqt,security}/
  gen_rules.py --check   fail (exit 1) on drift; exit 2 on a malformed source or a read/write failure
  gen_rules.py --self-test  assert an invalid-UTF-8 generated target fails closed (exit 2), that each
                            listed detail layout case gets its expected exit, and that each guard in
                            the revert table, put back in a scratch copy, changes its case's exit
"""
import html
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from _standards import dir_present, map_keys  # noqa: E402

TIER_FACETS = {"10": {"ACCUR", "INTEG", "QUALI", "TRUST"}, "20": {"PROGR"},
               "30": {"SPEED"}, "40": {"COST"}}
CIA_FACETS = {"SECC", "SECI", "SECA", "SECP"}  # SECC=Confidentiality SECI=Integrity SECA=Availability SECP=Privacy
# The global known-facet set: any rule may carry ANY of these as a `secondary` tag, cross-family (an aiqt
# rule may tag a security facet and vice versa). Security codes are namespaced (SEC*) so none collides with
# an AIQT facet (e.g. SECI vs the AIQT INTEG). The secondary element rules (known-facet, differs-from-
# primary, no-repeat) come from the Architect's recorded decision (design-of-record: secondary = any known
# facet), NOT spec section 4, which only types `secondary` as a sequence of facet-code strings.
KNOWN_FACETS = set().union(*TIER_FACETS.values()) | CIA_FACETS
# Keys allowed for EVERY rule family. secondary is NOT here: spec section 4 forbids it on the apex and
# allows it only on aiqt-non-apex and security, so it is added to those allow-sets individually.
BASE_KEYS = {"corpus-id", "origin", "family", "slug"}
# Optional standards-mapping keys, allowed on security and aiqt-non-apex rules. Each value is a flow
# sequence of external control/subcategory IDs, for the public /mappings crosswalk. Adding a mapping key
# never affects a rule's derived path. The set is DERIVED from the vendored manifests under
# .aiqt/standards/ (one key per manifest): a mapping key is valid only if its framework's pinned id
# manifest exists, so a rule can never cite an unsourced framework. check_mappings then validates each
# cited id against that manifest's enumerated set.
try:
    MAP_KEYS = map_keys(repo_root())
except OSError as _exc:
    # map_keys fails closed on an existing-but-unlistable .aiqt/standards/. This binding runs at import,
    # so convert that read error into a clean exit 2 with a message rather than a bare traceback; every
    # tool that imports gen_rules (against its OWN broken standards dir) then fails closed uniformly.
    print("error: cannot read {}/.aiqt/standards/ (fail-closed): {}".format(repo_root(), _exc),
          file=sys.stderr)
    raise SystemExit(2)
# Keys whose value, when present, must be a flow sequence (a list): secondary and every mapping key.
SEQ_KEYS = {"secondary"} | MAP_KEYS
SLUG_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
CID_RE = re.compile(r'^[a-z0-9]{6,}$')
# The two-layer split (see RULE SOURCE FORMAT above). DETAIL_HEADING is matched as a whole line. Any other
# body line whose visible text reads Detail is refused rather than read as core, so a near miss can never
# leave the author believing text was split when it was not.
DETAIL_HEADING = "## Detail"
DETAIL_KEYS = {"detail-trigger", "detail-reason"}
_DETAIL_NAMES = {"detail", "details"}
_CONTAINER_RE = re.compile(r'^[ \t]*(?:>|[-*+](?=[ \t]|$)|\d{1,9}[.)](?=[ \t]|$))[ \t]*')
_TAG_RE = re.compile(r'<[^>]*>')
_LINK_TAIL_RE = re.compile(r'\][ \t]*(?:\([^)]*\)|\[[^\]]*\])')
# The BODY GRAMMAR (see RULE SOURCE FORMAT above).
_HEADING_TEXT = frozenset(map(chr, range(0x20, 0x7f))) - frozenset("[]<>&\\~*_`")
_PROSE_TEXT = frozenset(map(chr, range(0x20, 0x7f))) - frozenset("<[]\\")
_PROSE_START = frozenset(" \t#>-+*=~|<[!_:")
_ORDERED_RE = re.compile(r'^[0-9]+[.)](?: |$)')
# The FRONTMATTER grammar (see RULE SOURCE FORMAT above). A raw frontmatter line holds no character of
# these Unicode categories, checked before any strip (str.strip() removes U+0085, U+2028, U+2029 and U+001C
# to U+001F, which YAML reads as line breaks; PyYAML refuses U+FFFE and U+FFFF, category Cn). ZWNJ and ZWJ
# (format characters that Persian and Indic text need) are the only exceptions.
_LINE_CATEGORIES = frozenset(("Cc", "Cf", "Cn", "Co", "Cs", "Zl", "Zp"))
_JOINERS = frozenset("\u200c\u200d")
# Words some YAML reader resolves to a boolean or a null, compared case-folded; a bare string is never one.
_YAML_WORDS = frozenset(("y", "n", "yes", "no", "true", "false", "on", "off", "null"))
# FREE TEXT: these Unicode categories (letters, marks, numbers, space separators), the joiners and this
# ASCII punctuation, nothing else.
_TEXT_CATEGORIES = frozenset(("Lu", "Ll", "Lt", "Lm", "Lo", "Mn", "Mc", "Me", "Nd", "Nl", "No", "Zs"))
_TEXT_PUNCT = frozenset(".,;()'/-")
_CODE_RE = re.compile(r'[a-z]+')
_FACET_RE = re.compile(r'[A-Z]{4,5}')
_TIER_RE = re.compile(r'10|20|30|40')
_CID_BARE_RE = re.compile(r'[a-z][a-z0-9]{5,}|[1-9][0-9]{5,}')
_MAP_ID_RE = re.compile(r'[A-Za-z][A-Za-z0-9.&()-]*(?: [A-Za-z0-9.&()-]+)*|[0-9]+(?:\.[0-9]+){2,}')
_MAP_QUOTED_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9.&()-]*(?: [A-Za-z0-9.&()-]+)*')
# The disclosed gap: a bare mapping id of this shape YAML reads as a float; _scalar accepts it only when the
# float's shortest decimal text (repr) is the id itself.
_MAP_FLOAT_RE = re.compile(r'(?:0|[1-9][0-9]{0,3})\.[0-9]{0,5}[1-9]')
# Each known key's declared value type; every map-* key is a mapping-id sequence (derive then checks it
# against MAP_KEYS).
_KEY_KINDS = {"corpus-id": "id", "origin": "code", "family": "code", "slug": "slug", "tier": "tier",
              "apex": "apex", "facet": "facet", "secondary": "facets", "detail-trigger": "text",
              "detail-reason": "text", "pack-id": "slug", "restates": "cids"}
_KIND_NAMES = {"id": "an id ([a-z][a-z0-9]{5,}, [1-9][0-9]{5,} or a quoted [a-z0-9]{6,})",
               "code": "a code ([a-z]+)", "slug": "a slug (kebab-case, a letter first when bare)",
               "tier": "a tier (10, 20, 30 or 40, bare)", "apex": "true or false (bare)",
               "facet": "a facet code ([A-Z]{4,5})", "facets": "a flow sequence of facet codes",
               "ids": "a flow sequence of mapping ids", "cids": "a flow sequence of ids",
               "text": ("free text (letters, digits, spaces and . , ; ( ) ' / - only, a letter first and no "
                        "space last)")}

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py; VER-CORE 6.5).
RENDERER_DECL = {"renderer-id": "rules", "semantics-revision": 1}
GENSRC_OUTPUTS = (
    {"target": ".claude/rules/aiqt/", "kind": "tree",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_rules.py"},
    {"target": ".claude/rules/security/", "kind": "tree",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_rules.py"},
)


def _quoted(tok):
    """The text inside one matching pair of quotes around tok, with no quote of that kind inside, or None."""
    if len(tok) >= 2 and tok[0] in "\"'" and tok[-1] == tok[0] and tok[0] not in tok[1:-1]:
        return tok[1:-1]
    return None


def _is_text(text):
    """True when text is FREE TEXT (see FRONTMATTER above)."""
    return (text[:1] != "" and unicodedata.category(text[0])[0] == "L"
            and unicodedata.category(text[-1]) != "Zs"
            and all(ch in _TEXT_PUNCT or ch in _JOINERS or unicodedata.category(ch) in _TEXT_CATEGORIES
                    for ch in text))


def _scalar(kind, tok):
    """(True, value) when tok is a scalar of the declared kind, else (False, None). Never models YAML: a
    shape not listed under FRONTMATTER is not accepted."""
    inner = _quoted(tok)
    if inner is not None:
        quoted_ok = {"id": CID_RE.match, "code": _CODE_RE.fullmatch, "slug": SLUG_RE.match,
                     "facet": _FACET_RE.fullmatch, "text": _is_text, "ids": _MAP_QUOTED_RE.fullmatch}.get(kind)
        return (True, inner) if quoted_ok is not None and quoted_ok(inner) else (False, None)
    if kind == "tier":
        return (True, int(tok)) if _TIER_RE.fullmatch(tok) else (False, None)
    if kind == "apex":
        return (True, tok == "true") if tok in ("true", "false") else (False, None)
    if kind == "id" and _CID_BARE_RE.fullmatch(tok):
        return True, int(tok) if tok.isdigit() else tok
    if kind == "ids" and _MAP_FLOAT_RE.fullmatch(tok) and repr(float(tok)) == tok:
        return True, tok
    bare_ok = {"code": _CODE_RE.fullmatch, "slug": lambda t: SLUG_RE.match(t) and t[0].isalpha(),
               "facet": _FACET_RE.fullmatch, "text": _is_text, "ids": _MAP_ID_RE.fullmatch}.get(kind)
    if bare_ok is not None and bare_ok(tok) and tok.casefold() not in _YAML_WORDS:
        return True, tok
    return False, None


def _value(key, v):
    """The value of frontmatter key from its text v (spaces stripped), by the key's declared type. Raises
    ValueError when the key has no declared type or v is not in a shape of it."""
    kind = "ids" if key.startswith("map-") else _KEY_KINDS.get(key)
    if kind is None:
        raise ValueError("unknown frontmatter key {!r} (no declared value type)".format(key))
    if kind in ("facets", "ids", "cids"):
        element = {"facets": "facet", "ids": "ids", "cids": "id"}[kind]
        if not (v.startswith("[") and v.endswith("]")):
            raise ValueError("frontmatter value of {} {!r} is not {}".format(key, v, _KIND_NAMES[kind]))
        inner = v[1:-1].strip(" ")
        elems = []
        for part in inner.split(",") if inner else ():
            ok, elem = _scalar(element, part.strip(" "))
            if not ok:
                raise ValueError("frontmatter value of {}: element {!r} is not {} (see FRONTMATTER)".format(
                    key, part.strip(" "), _KIND_NAMES[element] if element != "ids" else "a mapping id"))
            elems.append(elem)
        return elems
    ok, value = _scalar(kind, v)
    if not ok:
        raise ValueError("frontmatter value of {} {!r} is not {} (see FRONTMATTER)".format(key, v, _KIND_NAMES[kind]))
    return value


def decode_rule_source(raw, name, adopter=False):
    """A rule source's text from its raw bytes: UTF-8 with no newline translation. Raises ValueError on
    a CR byte (LF line endings only, as check_byte_canon requires) and on invalid UTF-8 (a
    UnicodeDecodeError, itself a ValueError). check_clauses decodes rule sources through this too. With
    adopter (ADOPTER MODE), each CRLF is first read as LF; a lone CR (classic Mac line endings) is still
    refused."""
    if adopter:
        raw = raw.replace(b"\r\n", b"\n")
    if b"\r" in raw:
        raise ValueError("{}: line {}: holds a CR byte; a rule source uses LF line endings only".format(
            name, raw.count(b"\n", 0, raw.index(b"\r")) + 1))
    return raw.decode("utf-8")


def read_rule_source(path, adopter=False):
    """Read one rule source the one way both gen_rules and check_clauses read it (see decode_rule_source)."""
    return decode_rule_source(path.read_bytes(), path.name, adopter)


def parse_source(path, adopter=False):
    """The frontmatter of one rule source as {key: value}, by the FRONTMATTER grammar; adopter selects
    ADOPTER MODE (the placement gate). Raises ValueError on a malformed source."""
    text = read_rule_source(path, adopter)
    if not text.startswith("---\n"):
        raise ValueError("{}: no frontmatter".format(path.name))
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("{}: unterminated frontmatter".format(path.name))
    fm = {}
    for raw in text[4:end].split("\n"):
        comment = raw.lstrip(" ").startswith("#")
        # ADOPTER MODE: a comment line may hold a tab after its `#` (YAML skips the line); every other
        # hidden character is still refused there, since YAML reads U+0085, U+2028 and U+2029 as breaks.
        scanned = raw.replace("\t", "") if adopter and comment else raw
        if any(unicodedata.category(ch) in _LINE_CATEGORIES and ch not in _JOINERS for ch in scanned):
            raise ValueError("{}: frontmatter line {!r} holds a hidden or unassigned character (Unicode "
                             "category Cc, Cf, Cn, Co, Cs, Zl or Zp; YAML can read a second key there or "
                             "refuse the file)".format(path.name, raw))
        if not raw.strip(" "):
            continue
        if comment:
            if adopter:
                continue
            raise ValueError("{}: frontmatter line {!r} is a YAML comment line (refused in the pack's own "
                             "rules: a renderer that does not read frontmatter can show it as a heading)"
                             .format(path.name, raw))
        if raw.startswith(" "):
            raise ValueError("{}: frontmatter line {!r} is indented; the frontmatter grammar requires each "
                             "key at column 0".format(path.name, raw))
        if ":" not in raw:
            raise ValueError("{}: bad frontmatter line {!r}".format(path.name, raw))
        key, val = raw.split(":", 1)
        key = key.strip(" ")
        if key in fm:
            raise ValueError("{}: duplicate key {}".format(path.name, key))
        if val[:1] != " ":
            raise ValueError("{}: frontmatter key {} is not followed by ': ' and a value (YAML reads the "
                             "line as one string, or an empty value as a null)".format(path.name, key))
        try:
            fm[key] = _value(key, val.strip(" "))
        except ValueError as exc:
            raise ValueError("{}: {}".format(path.name, exc))
    return fm


def _check_keys(fm, allowed, name):
    extra = set(fm) - allowed
    if extra:
        raise ValueError("{}: unknown or forbidden key(s): {}".format(name, ", ".join(sorted(extra))))


def _check_secondary(fm, primary, name):
    # `secondary` is optional; when present it is already validated as a list (SEQ_KEYS). Each element
    # must be a known facet code, must differ from the primary facet (a facet is not its own secondary),
    # and must not repeat. Fail-closed so a typo or a duplicate is caught at generation, not shipped.
    seen = set()
    for s in fm.get("secondary", []):
        if s not in KNOWN_FACETS:
            raise ValueError("{}: secondary facet '{}' is not a known facet code".format(name, s))
        if s == primary:
            raise ValueError("{}: secondary facet '{}' duplicates the primary facet".format(name, s))
        if s in seen:
            raise ValueError("{}: secondary facet '{}' listed more than once".format(name, s))
        seen.add(s)


def derive(fm, name, allowed_origins=("pack",)):
    # allowed_origins defaults to pack-only so the generator (which sources only pack rules from
    # .aiqt/core/) stays strict; the placement gate passes ("pack", "adopter") because it must accept
    # correctly-placed adopter-authored rules too (spec sections 3 and 4). Origin does not affect the
    # derived path, only which origins are valid.
    for req in ("corpus-id", "origin", "family", "slug"):
        if req not in fm:
            raise ValueError("{}: missing required key '{}'".format(name, req))
    if not CID_RE.match(str(fm["corpus-id"])):
        raise ValueError("{}: corpus-id must match ^[a-z0-9]{{6,}}$".format(name))
    if fm["origin"] not in allowed_origins:
        raise ValueError("{}: origin must be one of {}".format(name, "/".join(allowed_origins)))
    if not SLUG_RE.match(str(fm["slug"])):
        raise ValueError("{}: slug must be kebab-case".format(name))
    for k in SEQ_KEYS:
        if k in fm and not isinstance(fm[k], list):
            raise ValueError("{}: {} must be a flow sequence".format(name, k))
    family = fm["family"]
    if family == "aiqt":
        if fm.get("apex") is True:
            _check_keys(fm, BASE_KEYS | {"apex"}, name)
            if fm["slug"] != "project-integrity":
                raise ValueError("{}: apex slug must be 'project-integrity'".format(name))
            return "aiqt/00-project-integrity.md"
        _check_keys(fm, BASE_KEYS | {"tier", "facet", "secondary"} | MAP_KEYS | DETAIL_KEYS, name)
        tier = str(fm.get("tier", ""))
        facet = fm.get("facet", "")
        if tier not in TIER_FACETS:
            raise ValueError("{}: tier must be 10/20/30/40".format(name))
        if facet not in TIER_FACETS[tier]:
            raise ValueError("{}: facet '{}' invalid for tier {}".format(name, facet, tier))
        _check_secondary(fm, facet, name)
        return "aiqt/{}-{}-{}.md".format(tier, facet, fm["slug"])
    if family == "security":
        _check_keys(fm, BASE_KEYS | {"facet", "secondary"} | MAP_KEYS | DETAIL_KEYS, name)
        facet = fm.get("facet", "")
        if facet not in CIA_FACETS:
            raise ValueError("{}: security facet must be SECC/SECI/SECA/SECP".format(name))
        _check_secondary(fm, facet, name)
        return "security/{}-{}.md".format(facet, fm["slug"])
    raise ValueError("{}: unknown family '{}'".format(name, family))


def body_first_line(text, name):
    """The 1-based number of the first body line, the line after the frontmatter's closing `---`. Raises
    ValueError on a missing or unterminated frontmatter (the same frontmatter end parse_source uses)."""
    if not text.startswith("---\n"):
        raise ValueError("{}: no frontmatter".format(name))
    close = text.find("\n---\n", 4)
    if close == -1:
        raise ValueError("{}: unterminated frontmatter".format(name))
    return text.count("\n", 0, close + 5) + 1


def _uncontained(line):
    """The line with any leading blockquote and list item markers removed."""
    while True:
        container = _CONTAINER_RE.match(line)
        if not container:
            return line
        line = line[container.end():]


def _visible_text(line):
    """The line's visible text as RULE SOURCE FORMAT defines it: entities decoded, NFKC-normalized,
    format characters, leading container markers, HTML tags and link targets dropped, then only letters
    and digits kept, case-folded."""
    text = unicodedata.normalize("NFKC", html.unescape(line))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = _LINK_TAIL_RE.sub("", _TAG_RE.sub("", _uncontained(text)))
    return "".join(ch for ch in text if ch.isalnum()).casefold()


def _names_detail(line):
    """True when the line's visible text reads `detail` or `details`."""
    return _visible_text(line) in _DETAIL_NAMES


def _title_problem(line):
    """None when line is the BODY GRAMMAR title line, else what makes it not one."""
    if not line.startswith("# "):
        return "the first non-blank body line is the title line, '# ' and its text"
    if not line[2:].strip() or not set(line[2:]) <= _HEADING_TEXT:
        return ("the title text is printable ASCII, not blank, and holds none of [ ] < > & ~ * _, a "
                "backslash or a backtick")
    return None


def _prose_problem(line):
    """None when line is a BODY GRAMMAR prose line, else what makes it not one."""
    if line[0] in _PROSE_START or line.startswith("```"):
        return "a prose line starts with no space, tab, block marker or fence"
    if _ORDERED_RE.match(line):
        return "a prose line does not start like an ordered list item"
    if not set(line) <= _PROSE_TEXT:
        return "a prose line is printable ASCII holding no '<', '[', ']' or backslash"
    return None


def detail_heading_line(text, name):
    """The 1-based line number of the single accepted `## Detail` split in a rule source's body, or None
    when the source has none. Raises ValueError on each refused layout that RULE SOURCE FORMAT lists,
    other than the frontmatter keys and the empty detail layer (check_detail). check_clauses derives each
    clause's layer from it."""
    first = body_first_line(text, name)
    lines = text.split("\n")
    title = found = None
    for number in range(first, len(lines) + 1):
        line = lines[number - 1]
        if line == "":
            continue
        if title is None:
            title, problem = number, _title_problem(line)
        elif line == DETAIL_HEADING:
            if found is not None:
                raise ValueError("{}: more than one '{}' line (lines {} and {})".format(
                    name, DETAIL_HEADING, found, number))
            found = number
            continue
        else:
            problem = _prose_problem(line)
        if _names_detail(line):
            raise ValueError("{}: line {}: {!r} reads as Detail but is not the split line '{}'".format(
                name, number, line, DETAIL_HEADING))
        if problem is not None:
            raise ValueError("{}: line {}: {!r} is outside the body grammar ({})".format(
                name, number, line, problem))
    if title is None:
        raise ValueError("{}: the body has no title line ('# ' and its text)".format(name))
    if found is None:
        return None
    if found > first and lines[found - 2] != "":
        raise ValueError("{}: line {}: the '{}' split needs a blank line directly above it".format(
            name, found, DETAIL_HEADING))
    return found


def check_detail(text, fm, name):
    """Validate the two-layer split of one source: the detail keys go with exactly one `## Detail` split,
    detail-trigger is required with it, each key is a non-empty string, and the detail layer is not
    blank lines only (the core always holds the title line). Returns the split's line number, or None. Raises ValueError (a malformed source) on any violation."""
    heading = detail_heading_line(text, name)
    if heading is None:
        present = sorted(k for k in DETAIL_KEYS if k in fm)
        if present:
            raise ValueError("{}: {} without a '{}' heading".format(name, ", ".join(present), DETAIL_HEADING))
        return None
    if "detail-trigger" not in fm:
        raise ValueError("{}: a '{}' heading requires a detail-trigger key".format(name, DETAIL_HEADING))
    for key in sorted(DETAIL_KEYS & set(fm)):
        if not isinstance(fm[key], str) or not fm[key].strip():
            raise ValueError("{}: {} must be a non-empty string".format(name, key))
    lines = text.split("\n")
    if not "\n".join(lines[heading:]).strip():
        raise ValueError("{}: the '{}' layer is empty".format(name, DETAIL_HEADING))
    return heading


def load_corpus(src_dir):
    """Parse and fully validate every source (schema + unique corpus-id + unique derived path); raise
    ValueError on any malformed or duplicate. Returns [(path, frontmatter, derived_rel_path)]."""
    seen_ids, seen_paths, out = {}, {}, []
    # Collect *.md via os.walk with a raising onerror, NOT rglob: rglob (and a top-level-only
    # ensure_listable) SILENTLY skips an unreadable dir at ANY depth, so an unlistable family subdir
    # (e.g. .aiqt/core/rules/aiqt/) would read as an empty corpus (a false clean). os.walk(onerror=)
    # surfaces the read error at every level, so an unreadable dir fails closed as an OSError.
    def _raise(exc):
        raise exc
    md_files = []
    for dirpath, _dirs, filenames in os.walk(src_dir, onerror=_raise):
        md_files.extend(Path(dirpath) / fn for fn in filenames if fn.endswith(".md"))
    for src in sorted(md_files):
        fm = parse_source(src)
        rel = derive(fm, src.name)
        check_detail(read_rule_source(src), fm, src.name)
        cid = str(fm["corpus-id"])
        if cid in seen_ids:
            raise ValueError("{}: corpus-id {} already used by {}".format(src.name, cid, seen_ids[cid]))
        if rel in seen_paths:
            raise ValueError("{}: derives to {} already produced by {}".format(src.name, rel, seen_paths[rel]))
        seen_ids[cid] = src.name
        seen_paths[rel] = src.name
        out.append((src, fm, rel))
    return out


def run(root, check):
    """Reconcile the .claude/rules/ read tree under root against the .aiqt/core/rules/ corpus. Exit 0 in
    sync, 1 on drift (check mode), 2 on a malformed source or a read/write failure. Parameterized on root
    (rather than calling repo_root() inline) so the self-test can drive it against a synthetic tempdir
    tree, never the real repo."""
    src_dir = root / ".aiqt" / "core" / "rules"
    out_dir = root / ".claude" / "rules"
    desired = {}
    try:
        # dir_present (not is_dir) inside the try: an unreadable .aiqt/ parent must fail closed as exit 2,
        # not read as an absent corpus (which would delete every generated file as an orphan below).
        if dir_present(src_dir):
            for src, _fm, rel in load_corpus(src_dir):
                desired[rel] = src.read_text(encoding="utf-8")
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2
    # Reconcile even when src_dir is absent (desired empty) so orphaned generated files are never concealed.
    # The whole reconcile is fail-closed: an unreadable generated file (target.read_text) or an unreadable
    # generated dir at ANY depth (os.walk(onerror=raise), not rglob, which silently skips an unlistable
    # subdir) becomes a clean exit 2 rather than a traceback or a concealed orphan.
    drift = []

    def _raise(exc):
        raise exc
    try:
        for rel, content in sorted(desired.items()):
            target = out_dir / rel
            current = target.read_text(encoding="utf-8") if target.exists() else None
            if current != content:
                drift.append(rel)
                if not check:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
        for family in ("aiqt", "security"):
            fam_dir = out_dir / family
            if dir_present(fam_dir):  # not is_dir: an unreadable .claude parent must fail closed, not skip the orphan scan
                for dirpath, _dirs, filenames in os.walk(fam_dir, onerror=_raise):
                    for fn in sorted(f for f in filenames if f.endswith(".md")):
                        f = Path(dirpath) / fn
                        # as_posix(), not str(): desired keys are forward-slash derive() paths, so a
                        # backslash from str() on Windows would flag every generated file as an orphan.
                        rel = f.relative_to(out_dir).as_posix()
                        if rel not in desired:
                            drift.append("orphan " + rel)
                            if not check:
                                f.unlink()
    except (OSError, UnicodeError) as exc:
        # UnicodeError (UnicodeDecodeError) covers the generated-TARGET read above: a non-UTF-8 target
        # decodes as UTF-8 there, so a corrupt target fails closed (exit 2) rather than a raw traceback,
        # the same OSError path (a read-only fs, a permission error, a full disk) already fails closed on.
        print("error: {}".format(exc))
        return 2
    if check and drift:
        print("drift: " + "; ".join(drift))
        print("run tools/gen_rules.py to regenerate")
        return 1
    return 0


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test_main()
    return run(repo_root(), "--check" in argv)


# --- self-test ----------------------------------------------------------------------------------------
# One focused invariant (the sibling generators' idiom): an invalid-UTF-8 GENERATED TARGET fails closed
# (exit 2) rather than a raw UnicodeDecodeError traceback. The reconcile loop reads each desired target as
# UTF-8 (the drift compare), so a non-UTF-8 target must be caught by the widened (OSError, UnicodeError)
# arm (F-154). A revert to the narrow OSError-only arm makes run() RAISE instead of returning 2, so this
# case fails and guards the widening. Tempdir-only; never touches a real repo file.

_RULE_SRC = """---
corpus-id: selfr1
origin: pack
family: aiqt
tier: 10
facet: QUALI
slug: gen-rules-selftest-target
---
# Gen-rules self-test rule

A minimal rule so the reconcile has one desired target to read.
"""
_RULE_REL = "aiqt/10-QUALI-gen-rules-selftest-target.md"

# The two-layer split cases: (name, frontmatter key lines, body after the core paragraph, expected exit).
# _CASE_FRAME overrides the family lines, the slug, the corpus-id or the core text of a case; every other
# case is an aiqt rule whose core is an H1 and one paragraph.
_DETAIL_SRC = """---
corpus-id: {cid}
origin: pack
{family}slug: {slug}
{keys}---
{core}{body}"""
_AIQT_FAMILY = "family: aiqt\ntier: 10\nfacet: QUALI\n"
_CORE = "# Gen-rules detail self-test rule\n\nCore text.\n"
_TRIGGER = "detail-trigger: writing a self-test fixture\n"
_REASON = "detail-reason: self-test only, no must-fire clause moves\n"
_DETAIL_BODY = "\n## Detail\n\nDetail text.\n"
# An adopter-shaped rule (origin adopter, read the way check_rule_placement reads it: parse_source, then
# derive with the adopter origin allowed) whose string value holds an accented word.
_ADOPTER_NAME = "SECI-adopter-accented.md"
_ADOPTER_SRC = """---
corpus-id: adopt1
origin: adopter
family: security
facet: SECI
slug: adopter-accented
detail-trigger: reviewing a r\u00e9sum\u00e9 upload
---
# Adopter rule

Core text.

## Detail

Detail text.
"""
# ADOPTER MODE: an adopter rule with CRLF line endings, or with a comment line in its frontmatter (at
# column 0, indented, or holding a tab), passes the placement gate's read (parse_source in adopter mode,
# then derive with the adopter origin allowed, as check_rule_placement.check_drift does) and is refused by
# the pack's strict reader with a message holding the given text. conformance --self-test runs these same
# rules through C3.
_ADOPTER_MODE_REL = "aiqt/10-QUALI-team-review.md"
_ADOPTER_MODE_SRC = """---
corpus-id: teamrv
origin: adopter
family: aiqt
{comment}tier: 10
facet: QUALI
slug: team-review
---
# Team review

Core text.
"""
_ADOPTER_MODE_CASES = (
    ("adopter-crlf", _ADOPTER_MODE_SRC.format(comment="").replace("\n", "\r\n"), "CR byte"),
    ("adopter-comment", _ADOPTER_MODE_SRC.format(comment="# reviewed by the platform team, 2026-09\n"),
     "YAML comment"),
    ("adopter-indented-comment", _ADOPTER_MODE_SRC.format(comment="  # reviewed by the platform team\n"),
     "YAML comment"),
    ("adopter-tab-comment", _ADOPTER_MODE_SRC.format(comment="#\treviewed by the platform team\n"),
     "hidden or unassigned character"),
)
# Shapes ADOPTER MODE still refuses, with a refusal holding the given text: a lone CR, a tab before the
# `#`, a comment line holding a YAML line break (U+2028), after which YAML would read a key, and a
# frontmatter block indented alike (YAML reads a mapping; the grammar puts each key at column 0).
_ADOPTER_MODE_REFUSALS = (
    ("adopter-lone-cr", _ADOPTER_MODE_SRC.format(comment="").replace("\n", "\r"), "CR byte"),
    ("adopter-tab-before-comment", _ADOPTER_MODE_SRC.format(comment="\t# reviewed\n"),
     "hidden or unassigned character"),
    ("adopter-comment-line-separator",
     _ADOPTER_MODE_SRC.format(comment="# reviewed\u2028secondary: [TRUST]\n"), "hidden or unassigned character"),
    ("adopter-indented-block", "---\n" + "".join("  " + line + "\n" for line in
                                                  _ADOPTER_MODE_SRC.format(comment="").split("\n")[1:7])
     + "---\n# Team review\n\nCore text.\n", "is indented; the frontmatter grammar requires each key at column 0"),
)
# Red on revert for ADOPTER MODE: with the guard put back (or, for a refusal, loosened), the placement
# gate's read of the case flips. (name, case, fixed text, reverted text)
_ADOPTER_MODE_REVERTS = (
    ("adopter-crlf", "adopter-crlf", '        raw = raw.replace(b"\\r\\n", b"\\n")\n', "        pass\n"),
    ("adopter-comment", "adopter-comment", "        if comment:\n            if adopter:",
     "        if comment:\n            if False:"),
    ("adopter-indented-comment", "adopter-indented-comment", 'comment = raw.lstrip(" ").startswith("#")',
     'comment = raw.startswith("#")'),
    ("adopter-tab-comment", "adopter-tab-comment", 'scanned = raw.replace("\\t", "") if adopter and comment',
     "scanned = raw if adopter and comment"),
    ("adopter-tab-before-comment", "adopter-tab-before-comment", 'comment = raw.lstrip(" ").startswith("#")',
     'comment = raw.lstrip(" \\t").startswith("#")'),
    ("adopter-comment-line-separator", "adopter-comment-line-separator",
     'scanned = raw.replace("\\t", "") if adopter and comment', 'scanned = "" if adopter and comment'),
)


# Red on revert for the ADOPTER MODE PyYAML comparison: ADOPTER MODE made to drop a key (the facet line
# skipped as a comment). (fixed text, reverted text)
_ADOPTER_DROP_KEY = ('comment = raw.lstrip(" ").startswith("#")',
                     'comment = raw.lstrip(" ").startswith(("#", "facet") if adopter else "#")')


def _yaml_compare(name, mine, theirs, failures):
    """Append to failures each way PyYAML's frontmatter mapping theirs differs from parse_source's mine,
    apart from the disclosed float gap. Returns the number of disclosed float-gap ids."""
    gaps = 0
    if not isinstance(theirs, dict) or set(mine) != set(theirs):
        failures.append("{}: keys differ: {} vs {}".format(
            name, sorted(mine), sorted(theirs) if isinstance(theirs, dict) else repr(theirs)))
        return 0
    for key, value in mine.items():
        other = theirs[key]
        pairs = list(zip(value, other)) if isinstance(value, list) and isinstance(other, list) and \
            len(value) == len(other) else [(value, other)]
        for a, b in pairs:
            if type(a) is type(b) and a == b:
                continue
            if isinstance(a, str) and type(b) is float and _MAP_FLOAT_RE.fullmatch(a) and repr(b) == a:
                gaps += 1
                continue
            failures.append("{}: {}: {!r} here, {!r} to YAML".format(name, key, value, other))
            break
    return gaps


def _yaml_adopter_agreement(paths):
    """Check that PyYAML safe_load reads the same frontmatter keys and values as the placement gate's read
    (parse_source in ADOPTER MODE) for each adopter rule file in paths. YAML is given the frontmatter as
    raw text, CRLF and comment lines included. Returns the failures, or None when PyYAML is not importable."""
    try:
        import yaml
    except ImportError:
        return None
    failures = []
    for path in paths:
        found = re.match(r"---\r?\n(.*?\r?\n)---\r?\n", path.read_bytes().decode("utf-8"), re.S)
        try:
            mine = parse_source(path, adopter=True)
        except ValueError as exc:
            failures.append("{}: refused in ADOPTER MODE: {}".format(path.name, exc))
            continue
        if found is None:
            failures.append("{}: no frontmatter for YAML".format(path.name))
            continue
        _yaml_compare(path.name, mine, yaml.safe_load(found.group(1)), failures)
    return failures


def _yaml_agreement(root, tmp):
    """Check that PyYAML safe_load reads the same frontmatter keys and values as parse_source, for every
    live source under root and a deterministic fuzz of accepted values. Returns (failures, live count,
    fuzz count, disclosed float-gap count), or None when PyYAML is not importable."""
    try:
        import yaml
    except ImportError:
        return None
    import random

    def agree(path, failures):
        text = path.read_text(encoding="utf-8")
        return _yaml_compare(path.name, parse_source(path), yaml.safe_load(text[4:text.index("\n---\n", 4)]),
                             failures)

    failures, gaps, live = [], 0, 0
    src_dir = root / ".aiqt" / "core" / "rules"
    for path in sorted(src_dir.rglob("*.md")) if src_dir.is_dir() else ():
        gaps += agree(path, failures)
        live += 1
    rng = random.Random(392)
    letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ\u00e9\u0436\u0645\u4e2d\u0d28"
    text_pool = letters + "0123456789 .,;()'/-\u0301\u0661\u00a0\u3000\u200c\u200d"
    id_pool = "ABCZaz09.&()-"

    def pick(make, kind):
        while True:
            value = make()
            if kind is None or _scalar(kind, value)[0]:
                return value

    def quote(value):
        mark = rng.choice("\"'")
        return mark + value + mark if mark not in value and rng.random() < 0.3 else value

    def text():
        return rng.choice(letters) + "".join(rng.choice(text_pool) for _ in range(rng.randrange(12))) + \
            rng.choice(letters + "0123456789.)")

    def map_id():
        form = rng.randrange(4)
        if form == 0:
            return "{}.{}".format(rng.choice(("0", "6", "10", "8", "123")), rng.choice(("7", "2", "01", "305")))
        if form == 1:
            return ".".join(str(rng.randrange(20)) for _ in range(rng.randrange(3, 5)))
        word = rng.choice(letters[:52]) + "".join(rng.choice(id_pool) for _ in range(rng.randrange(6)))
        return word + (" " + "".join(rng.choice(id_pool) for _ in range(rng.randrange(1, 4))) if form == 2 else "")

    makers = (("corpus-id", lambda: rng.choice((quote("abc" + str(rng.randrange(10 ** 6))),
                                                str(rng.randrange(10 ** 5, 10 ** 8)), "'0123456'")), "id"),
              ("origin", lambda: quote(rng.choice(("pack", "adopter"))), "code"),
              ("family", lambda: quote(rng.choice(("aiqt", "security"))), "code"),
              ("slug", lambda: quote(rng.choice(("a-b", "on-call", "x1-2y", "rule"))), "slug"),
              ("tier", lambda: rng.choice(("10", "20", "30", "40")), "tier"),
              ("apex", lambda: rng.choice(("true", "false")), "apex"),
              ("facet", lambda: quote(rng.choice(sorted(KNOWN_FACETS))), "facet"),
              ("detail-trigger", lambda: quote(text()), "text"),
              ("detail-reason", lambda: quote(text()), "text"))
    fuzz = 1500
    for number in range(fuzz):
        lines = ["{}: {}{}".format(key, pick(make, kind), " " * rng.randrange(2)) for key, make, kind in makers]
        lines.append("secondary: [{}]".format(", ".join(quote(rng.choice(sorted(KNOWN_FACETS)))
                                                        for _ in range(rng.randrange(4)))))
        lines.append("map-x-broad: [{}]".format(" ,".join(quote(pick(map_id, "ids"))
                                                          for _ in range(rng.randrange(1, 5)))))
        rng.shuffle(lines)
        path = tmp / "fuzz-{}.md".format(number)
        path.write_bytes(("---\n" + "\n".join(lines) + "\n---\n# T\n").encode("utf-8"))
        gaps += agree(path, failures)
    return failures, live, fuzz, gaps


_R6_FENCED_COMMENT = "\n```\n<!--\n```\nDeta<span\nclass=\"x\">il</span>\n---\nHidden.\n-->\n"
_R6_ORDERED_SETEXT = "\nDetail[](x '\n2. # y')\n---\nHidden.\n"
_DETAIL_CASES = (
    ("detail-with-trigger", _TRIGGER + _REASON, _DETAIL_BODY, 0),
    ("security-detail", _TRIGGER, _DETAIL_BODY, 0),
    ("fenced-code-detail", _TRIGGER, "\n## Detail\n\n```\ncode\n```\n", 2),
    ("closed-fence-and-comment", "", "\n```text\ncode\n```\n\n~~~~\ncode\n~~~~\n\n<!--\nnote\n-->\n", 2),
    ("detail-without-trigger", _REASON, _DETAIL_BODY, 2),
    ("trigger-without-detail", _TRIGGER, "", 2),
    ("reason-without-detail", _REASON, "", 2),
    ("two-detail-headings", _TRIGGER, _DETAIL_BODY + "\n## Detail\n\nMore detail.\n", 2),
    ("near-miss-heading", "", "\n### Detail\n\nDetail text.\n", 2),
    ("near-miss-lowercase", "", "\n## detail\n\nDetail text.\n", 2),
    ("near-miss-uppercase", "", "\n## DETAILS\n", 2),
    ("near-miss-two-spaces", "", "\n##  Detail\n\nDetail text.\n", 2),
    ("near-miss-colon", "", "\n## Detail:\n\nDetail text.\n", 2),
    ("near-miss-plural", "", "\n## Details\n\nDetail text.\n", 2),
    ("indented-heading", "", "\n    ## Detail\n\nDetail text.\n", 2),
    ("blockquote-heading", "", "\n> ## Detail\n\nDetail text.\n", 2),
    ("list-item-heading", "", "\n- ## Detail\n\nDetail text.\n", 2),
    ("ordered-list-heading", "", "\n1. ## Detail\n\nDetail text.\n", 2),
    ("setext-heading", "", "\nDetail\n------\n\nDetail text.\n", 2),
    ("setext-heading-h1", "", "\ndetails\n===\n\nDetail text.\n", 2),
    ("backtick-fenced-heading", _TRIGGER, "\n```\n## Detail\n```\n\nDetail text.\n", 2),
    ("tilde-fenced-heading", _TRIGGER, "\n~~~~\n## Detail\n~~~~~\n\nDetail text.\n", 2),
    ("indented-fence-close", _TRIGGER, "\n```\ncode\n   ```\n" + _DETAIL_BODY, 2),
    ("fenced-near-miss", "", "\n````md\n## Details\n````\n", 2),
    ("comment-heading", _TRIGGER, "\n<!--\n## Detail\n-->\n\nDetail text.\n", 2),
    ("comment-near-miss", "", "\n<!-- a note\n### Detail\n-->\n", 2),
    ("unterminated-fence", _TRIGGER, "\n```\ncode\n" + _DETAIL_BODY, 2),
    ("unterminated-comment", _TRIGGER, "\n<!-- note\n" + _DETAIL_BODY, 2),
    ("no-split-unterminated-fence", "", "\n```\ncode\n", 2),
    ("cr-byte", "", "\nCore line two.\r\n", 2),
    ("empty-core", _TRIGGER, _DETAIL_BODY, 2),
    ("comment-only-core", _TRIGGER, _DETAIL_BODY, 2),
    ("empty-detail", _TRIGGER, "\n## Detail\n", 2),
    ("comment-only-detail", _TRIGGER, "\n## Detail\n\n<!-- nothing -->\n\n", 2),
    ("empty-comment-detail", _TRIGGER, "\n## Detail\n\n<!-->\n", 2),
    ("non-string-trigger", "detail-trigger: [writing]\n", _DETAIL_BODY, 2),
    ("longer-fence-close", _TRIGGER, "\n````md\n```\n## Detail\n```\n````\n", 2),
    ("mixed-fence-close", _TRIGGER, "\n```\n~~~\n## Detail\n~~~\n```\n", 2),
    ("backtick-info-not-fence", _TRIGGER, "\n```not` a fence\n" + _DETAIL_BODY, 2),
    ("html-pre", _TRIGGER, "\n<pre>\n## Detail\n</pre>\n\nDetail text.\n", 2),
    ("html-div", _TRIGGER, "\n<div>\n## Detail\n</div>\n\nDetail text.\n", 2),
    ("html-script", _TRIGGER, "\n<script>\n## Detail\n</script>\n\nDetail text.\n", 2),
    ("html-details", _TRIGGER, "\n<details>\n\n## Detail\n\nDetail text.\n\n</details>\n", 2),
    ("html-close-tag", _TRIGGER, "\n</div>\n## Detail\n\nDetail text.\n", 2),
    ("html-processing", _TRIGGER, "\n<?x\n## Detail\n?>\n\nDetail text.\n", 2),
    ("html-declaration", _TRIGGER, "\n<!X\n## Detail\n>\n\nDetail text.\n", 2),
    ("html-heading-near-miss", "", "\n<h2>Detail</h2>\n", 2),
    ("blockquote-setext", "", "\n> Detail\n> ---\n", 2),
    ("list-setext", "", "\n- Detail\n  ---\n", 2),
    ("multiline-setext", "", "\nDetail\nnotes\n---\n", 2),
    ("bold-setext", "", "\n**Details**\n===\n", 2),
    ("bold-near-miss", "", "\n## **Detail**\n", 2),
    ("underscore-near-miss", "", "\n## _Detail_\n", 2),
    ("backtick-near-miss", "", "\n## `Detail`\n", 2),
    ("code-span-comment-opener", _TRIGGER, "\nUse `<!--` to open a comment.\n" + _DETAIL_BODY, 2),
    ("empty-comment", _TRIGGER, "\n<!-->\n" + _DETAIL_BODY, 2),
    ("link-heading", "", "\n## [Detail](https://example.test)\n\nTail.\n", 2),
    ("entity-heading", "", "\n## Deta&#105;l\n\nTail.\n", 2),
    ("inline-html-heading", "", "\n## <em>Detail</em>\n\nTail.\n", 2),
    ("escape-heading", "", "\n## \\*Detail\\*\n\nTail.\n", 2),
    ("strikethrough-heading", "", "\n## ~~Detail~~\n\nTail.\n", 2),
    ("link-setext", "", "\n[Detail](https://example.test)\n---\n\nTail.\n", 2),
    ("fullwidth-heading", "", "\n## \uff24\uff45\uff54\uff41\uff49\uff4c\n", 2),
    ("invisible-before-marker", "", "\n\u200b10. Detail\n", 2),
    ("plain-setext", _TRIGGER, "\nOverview\n===\n" + _DETAIL_BODY, 2),
    ("no-split-setext", "", "\nOverview\n===\n\nTail.\n", 2),
    ("heading-then-rule", "", "\nDetail is discussed here.\n# Another heading\n---\n", 2),
    ("blank-then-rule", "", "\nDetail is discussed here.\n\n---\n", 2),
    ("fence-then-rule", "", "\n```\nDetail is here\n```\n---\n", 2),
    ("comment-then-rule", "", "\n<!-- Detail is here -->\n---\n", 2),
    ("rule-then-rule", "", "\nText.\n\n***\n---\n", 2),
    ("list-fence", _TRIGGER, "\n- a\n\n  ```\nx\n```\n## Detail\n\nDetail text.\n", 2),
    ("list-fence-comment", _TRIGGER, "\n- a\n\n  ```\nx\n<!--\n```\n## Detail\n\nDetail text.\n-->\n", 2),
    ("list-comment", _TRIGGER, "\n- a\n\n  <!--\n```\n-->\n## Detail\n\nDetail text.\n```\n```\n", 2),
    ("blockquote-fence", _TRIGGER, "\n> ```\n> x\n> ```\n" + _DETAIL_BODY, 2),
    ("indented-fence", _TRIGGER, "\n  ```\n  x\n  ```\n" + _DETAIL_BODY, 2),
    ("indented-comment", _TRIGGER, "\n  <!-- note -->\n\nTail.\n" + _DETAIL_BODY, 2),
    # QA round 4: every reproduced finding of both reviews exits 2.
    ("r4-setext-dash-space", "", "\nDetail\n- \n", 2),
    ("r4-setext-dash-tab", "", "\nDetail\n-\t\n", 2),
    ("r4-quote-setext-dash", "", "\n> Detail\n> - \n", 2),
    ("r4-list-setext-dash", "", "\n- Detail\n  - \n", 2),
    ("r4-quoted-script", _TRIGGER, "\n> <script>\n## Detail\nDetail obligation.\n> </script>\n", 2),
    ("r4-quoted-script-blank", _TRIGGER,
     "\n> <script>\n\n## Detail\n\nDetail obligation.\n> </script>\n", 2),
    ("r4-comment-reopen", _TRIGGER,
     "\n<!-- a --> <!--\n```\n-->\n## Detail\n\ndetail text\n<!--\n```\nmore -->\n", 2),
    ("r4-comment-reopen-next", _TRIGGER,
     "\n<!--\na --> b <!--\n```\n-->\n## Detail\n\ndetail text\n<!--\n```\nmore -->\n", 2),
    ("r4-comment-reopen-blank", _TRIGGER,
     "\n<!-- a --> <!--\n```\n-->\n\n## Detail\n\ndetail text\n```\n", 2),
    ("r4-comment-reopen-setext", "", "\n<!-- a --> <!--\n```\n-->\nDetail\n---\n```\n", 2),
    ("r4-comment-reopen-link", "", "\n<!-- a --> <!--\n```\n-->\n## [Detail](x)\n```\n", 2),
    ("r4-wide-ordered-setext", "", "\n10. Detail\n    ---\n", 2),
    ("r4-wide-ordered-setext-h1", "", "\n10. Detail\n    ===\n", 2),
    ("r4-wide-bullet-setext", "", "\n-   Detail\n    ---\n", 2),
    ("r4-wide-one-setext", "", "\n1.  Detail\n    ---\n", 2),
    ("r4-nested-list-setext", "", "\n- a\n  - Detail\n    ---\n", 2),
    ("r4-tab-list-setext", "", "\n-\tDetail\n\t---\n", 2),
    ("r4-zwsp-lead", "", "\n## \u200bDetail\n", 2),
    ("r4-zwsp-inner", "", "\n## De\u200btail\n", 2),
    ("r4-soft-hyphen", "", "\n## De\u00adtail\n", 2),
    # QA round 5: every reproduced finding of both reviews exits 2 (PLAIN BODY, D-392-PLAIN-BODY).
    ("r5-balanced-link-heading", "", "\n## [Detail](https://example.test/a(b)c)\n\nTail.\n", 2),
    ("r5-comment-gt-heading", "", "\n## Detail <!-- > hidden -->\n\nTail.\n", 2),
    ("r5-multiline-html-heading", "", "\n<h2>De<span\ntitle=\"x\">tail</span></h2>\n\nTail.\n", 2),
    ("r5-multiline-comment-setext", "", "\nDeta<!--\n-->il\n---\n\nTail.\n", 2),
    ("r5-multiline-span-setext", "", "\nDeta<span\nclass=\"x\">il</span>\n---\n\nTail.\n", 2),
    ("r5-hangul-filler", "", "\n## Detail\u3164\n\nTail.\n", 2),
    ("r5-halfwidth-hangul-filler", "", "\n## Detail\uffa0\n\nTail.\n", 2),
    ("r5-cyrillic-e", "", "\n## D\u0435tail\n\nTail.\n", 2),
    ("r5-detail-reference-definition", _TRIGGER,
     "\n## Detail\n\n[the policy]: https://example.test/p\n\nDetail text.\n", 2),
    # D-392-PLAIN-BODY-UNBLOCK: a title with parentheses and no bracket is plain, with or without the split
    # (the title is set by _CASE_FRAME). Since D-392-ENUMERATED-BODY-GRAMMAR no other heading is allowed.
    ("r5-paren-heading", "", "", 0),
    ("r5-paren-heading-split", _TRIGGER, _DETAIL_BODY, 0),
    ("r5-paren-subheading", _TRIGGER, "\n## Scope (core)\n" + _DETAIL_BODY + "\n### Notes (a)(b) ()\n", 2),
    # Before D-392-ENUMERATED-BODY-GRAMMAR these two exited 0; a sub-heading and a list are now refused,
    # and the prose shapes the live corpus uses are admitted (exit 0).
    ("plain-prefix-ok", _TRIGGER, "\nText with (parens), 'quotes' and `code`.\n\"Quoted\" start.\n2024 was a year."
     "\n`GIT_`-prefixed text, a > b, a | b, snake_case & co, 1.5 and 2.\n" + _DETAIL_BODY, 0),
    ("plain-list-ok", _TRIGGER, "\n- item one\n  continued\n* item two\n1. item three\n   continued\n\n"
     "10. item four\n    continued\n" + _DETAIL_BODY, 2),
    ("plain-subheading", _TRIGGER, "\n## Scope\n" + _DETAIL_BODY, 2),
    ("plain-blank-before", _TRIGGER, "\nCore line two.\n## Detail\n\nDetail text.\n", 2),
    ("plain-start-plus", _TRIGGER, "\n+ item\n" + _DETAIL_BODY, 2),
    ("plain-start-quote", _TRIGGER, "\n> quoted\n" + _DETAIL_BODY, 2),
    ("plain-start-table", _TRIGGER, "\n| a | b |\n" + _DETAIL_BODY, 2),
    ("plain-start-rule", _TRIGGER, "\n***\n" + _DETAIL_BODY, 2),
    ("plain-heading-seven", _TRIGGER, "\n####### Seven\n" + _DETAIL_BODY, 2),
    ("plain-tab", _TRIGGER, "\nCore\ttext.\n" + _DETAIL_BODY, 2),
    ("plain-lt", _TRIGGER, "\nCore a < b.\n" + _DETAIL_BODY, 2),
    ("plain-fence-marker", _TRIGGER, "\n```\ncode\n```\n" + _DETAIL_BODY, 2),
    ("plain-tilde-marker", _TRIGGER, "\nText ~~~ more.\n" + _DETAIL_BODY, 0),  # not at the line start: no fence
    ("plain-list-paren", _TRIGGER, "\n1) item\n" + _DETAIL_BODY, 2),
    ("plain-nested-ordered", _TRIGGER, "\n- a\n  1. nested\n" + _DETAIL_BODY, 2),
    ("plain-heading-empty", _TRIGGER, "\n## \n" + _DETAIL_BODY, 2),
    ("plain-heading-bracket", _TRIGGER, "\n## See [x]\n" + _DETAIL_BODY, 2),
    ("plain-heading-lt", _TRIGGER, "\n## See <x>\n" + _DETAIL_BODY, 2),
    ("plain-heading-amp", _TRIGGER, "\n## Fish &amp; chips\n" + _DETAIL_BODY, 2),
    ("plain-heading-backslash", _TRIGGER, "\n## Path \\x\n" + _DETAIL_BODY, 2),
    ("plain-heading-tilde", _TRIGGER, "\n## ~~Old~~\n" + _DETAIL_BODY, 2),
    ("plain-heading-star", _TRIGGER, "\n## *Key*\n" + _DETAIL_BODY, 2),
    ("plain-heading-underscore", _TRIGGER, "\n## _Key_\n" + _DETAIL_BODY, 2),
    ("plain-heading-backtick", _TRIGGER, "\n## `key`\n" + _DETAIL_BODY, 2),
    ("plain-heading-tab", _TRIGGER, "\n## A\tb\n" + _DETAIL_BODY, 2),
    ("plain-indented-code", _TRIGGER, "\n    code\n" + _DETAIL_BODY, 2),
    ("plain-item-indent", _TRIGGER, "\n10. a\n  bcde\n" + _DETAIL_BODY, 2),
    ("plain-item-text", _TRIGGER, "\n- > quoted\n" + _DETAIL_BODY, 2),
    # QA round 6: both blockers and the frontmatter finding exit 2 (D-392-ENUMERATED-BODY-GRAMMAR).
    ("r6-fenced-comment", "", _R6_FENCED_COMMENT, 2),
    ("r6-fenced-comment-split", _TRIGGER, _DETAIL_BODY + _R6_FENCED_COMMENT, 2),
    ("r6-fenced-comment-refdef", _TRIGGER, _DETAIL_BODY + "\n```\n<!--\n```\n[the policy]: https://example.test/p\n-->\n",
     2),
    ("r6-fenced-hangul", "", "\n```\n<!--\n```\n## Detail\u3164\n-->\n", 2),
    ("r6-ordered-setext", "", _R6_ORDERED_SETEXT, 2),
    ("r6-ordered-setext-split", _TRIGGER, _DETAIL_BODY + _R6_ORDERED_SETEXT, 2),
    ("r6-ordered-break-setext", "", "\nPara\n2. ***\n---\n", 2),
    ("r6-ordered-fence-setext", "", "\nPara\n2. ```\n---\n", 2),
    ("r6-ordered-paren-setext", "", "\nPara\n10) # x\n===\n", 2),
    ("r6-frontmatter-heading", "## Detail\n", "", 2),
    ("r6-frontmatter-indented-comment", "  # a note\n", "", 2),
    # QA round 7: a frontmatter value holds no `<`, `[`, `]` or backslash, so no HTML or link in it adds a
    # Detail heading or hides one where frontmatter renders as Markdown.
    ("r7-frontmatter-html-heading", "detail-trigger: testing <h2>Detail</h2>\n", _DETAIL_BODY, 2),
    ("r7-frontmatter-style", "detail-trigger: testing <style>\n", _DETAIL_BODY, 2),
    ("r7-frontmatter-quoted-html", _TRIGGER + 'detail-reason: "see <b>x</b>"\n', _DETAIL_BODY, 2),
    ("r7-frontmatter-link", _TRIGGER + "detail-reason: see [the policy]\n", _DETAIL_BODY, 2),
    ("r7-frontmatter-backslash", _TRIGGER + "detail-reason: a \x5c b\n", _DETAIL_BODY, 2),
    ("r7-frontmatter-sequence-ok", "secondary: [INTEG]\n" + _TRIGGER, _DETAIL_BODY, 0),
    # Other non-ASCII text in a value is accepted (it forms no HTML or link); control and format
    # characters and line separators are refused, and a number is ASCII digits only.
    ("r7-frontmatter-non-ascii", _TRIGGER + "detail-reason: caf\u00e9\n", _DETAIL_BODY, 0),
    ("r7b-frontmatter-tab", _TRIGGER + "detail-reason: a\tb\n", _DETAIL_BODY, 2),
    ("r7b-frontmatter-format", _TRIGGER + "detail-reason: a\u200bb\n", _DETAIL_BODY, 2),
    ("r7b-frontmatter-line-separator", _TRIGGER + "detail-reason: a\u2028b\n", _DETAIL_BODY, 2),
    ("r7b-frontmatter-paragraph-separator", _TRIGGER + "detail-reason: a\u2029b\n", _DETAIL_BODY, 2),
    ("r7b-frontmatter-separator-key", "detail-reason: a\u2028detail-trigger: x\n", _DETAIL_BODY, 2),
    ("r7b-frontmatter-non-ascii-tier", "", "", 2),
    # QA round 8: the hidden-character check reads the raw frontmatter line before any strip, so a
    # separator at a value's edge (which str.strip() removes and YAML reads as a line break) is refused.
    ("r8-f1-paragraph-separator-slug", _TRIGGER + "detail-reason:\u2029slug: other-slug\n", _DETAIL_BODY, 2),
    ("r8-f1-next-line-key", _TRIGGER + "detail-reason:\x85detail-x: y\n", _DETAIL_BODY, 2),
    ("r8-f1-line-separator-trigger", "detail-reason:\u2028detail-trigger: hidden key\n", _DETAIL_BODY, 2),
    ("r8-codex-line-separator-key", "detail-trigger:\u2028injected: surprise\n", _DETAIL_BODY, 2),
    ("r8-raw-leading-next-line", _TRIGGER + "detail-reason: \x85x\n", _DETAIL_BODY, 2),
    ("r8-raw-trailing-separator", _TRIGGER + "detail-reason: x\x1c\n", _DETAIL_BODY, 2),
    ("r8-raw-trailing-line-separator", _TRIGGER + "detail-reason: x\u2028\n", _DETAIL_BODY, 2),
    ("r8-zwnj-persian", _TRIGGER + "detail-reason: \u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645\n", _DETAIL_BODY, 0),
    ("r8-zwj-emoji", _TRIGGER + "detail-reason: pairing \U0001f469\u200d\U0001f4bb\n", _DETAIL_BODY, 2),
    ("r9-zwj-malayalam", _TRIGGER + "detail-reason: \u0d28\u0d4d\u200d text\n", _DETAIL_BODY, 0),
    # A value YAML would read differently from this tool is refused: a null or boolean word, ' #' (a YAML
    # comment), a leading YAML indicator, a quote that does not close exactly at the end, a mapping
    # separator, a key with no space after its colon, and an indented line.
    ("r8-null-tilde", "detail-trigger: ~\n", _DETAIL_BODY, 2),
    ("r8-null-lower", "detail-trigger: null\n", _DETAIL_BODY, 2),
    ("r8-null-title", "detail-trigger: Null\n", _DETAIL_BODY, 2),
    ("r8-null-upper", "detail-trigger: NULL\n", _DETAIL_BODY, 2),
    ("r8-bool-yes", "detail-trigger: yes\n", _DETAIL_BODY, 2),
    ("r8-value-equals", "detail-trigger: =\n", _DETAIL_BODY, 2),
    ("r8-comment", "detail-trigger: writing x #hidden part\n", _DETAIL_BODY, 2),
    ("r8-start-folded", "detail-trigger: > x\n", _DETAIL_BODY, 2),
    ("r8-start-literal", "detail-trigger: | x\n", _DETAIL_BODY, 2),
    ("r8-start-anchor", "detail-trigger: &a x\n", _DETAIL_BODY, 2),
    ("r8-start-alias", "detail-trigger: *a\n", _DETAIL_BODY, 2),
    ("r8-start-tag", "detail-trigger: !!str x\n", _DETAIL_BODY, 2),
    ("r8-start-percent", "detail-trigger: %x\n", _DETAIL_BODY, 2),
    ("r8-start-at", "detail-trigger: @x\n", _DETAIL_BODY, 2),
    ("r8-start-backtick", "detail-trigger: `x`\n", _DETAIL_BODY, 2),
    ("r8-start-brace", "detail-trigger: {x}\n", _DETAIL_BODY, 2),
    ("r8-start-comma", "detail-trigger: , x\n", _DETAIL_BODY, 2),
    ("r8-start-question", "detail-trigger: ? x\n", _DETAIL_BODY, 2),
    ("r8-start-dash", "detail-trigger: - x\n", _DETAIL_BODY, 2),
    ("r8-dash-word", "detail-trigger: -x writing\n", _DETAIL_BODY, 2),
    ("r8-quote-unclosed", "detail-trigger: 'writing\n", _DETAIL_BODY, 2),
    ("r8-quote-doubled", "detail-trigger: 'it''s'\n", _DETAIL_BODY, 2),
    ("r8-mapping-separator", "detail-trigger: a: b\n", _DETAIL_BODY, 2),
    ("r8-final-colon", "detail-trigger: writing:\n", _DETAIL_BODY, 2),
    ("r8-url", "detail-trigger: see https://example.test/x\n", _DETAIL_BODY, 2),
    ("r8-no-space-after-colon", "detail-trigger:writing\n", _DETAIL_BODY, 2),
    ("r8-indented-under-empty", "detail-reason:\n  detail-trigger: x\n", _DETAIL_BODY, 2),
    ("r8-indented-under-folded", "detail-reason: >\n  detail-trigger: x\n", _DETAIL_BODY, 2),
    ("r8-indented-under-value", "detail-trigger: a\n  detail-reason: b\n", _DETAIL_BODY, 2),
    ("r8-element-null", "map-iso-42001-broad: [A.5.1, ~]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r8-element-pair", "map-iso-42001-broad: [A.5.1, a: b]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r8-element-brace", "map-iso-42001-broad: [A.5.1, a{b]\n" + _TRIGGER, _DETAIL_BODY, 2),
    # QA round 9 (D-392-FRONTMATTER-TYPED-GRAMMAR): each key has a declared value type, and only its
    # shapes are accepted; the round-9 reproductions exit 2 and the declared shapes exit 0.
    ("r9-tier-leading-zero", "", "", 2),
    ("r9-tier-quoted", "", "", 2),
    ("r9-corpus-id-leading-zero", "", "", 2),
    ("r9-corpus-id-digits-ok", "", "", 0),
    ("r9-corpus-id-quoted-ok", "", "", 0),
    ("r9-noncharacter-fffe", "detail-trigger: editing \ufffe files\n", _DETAIL_BODY, 2),
    ("r9-noncharacter-ffff", "detail-trigger: \uffff\n", _DETAIL_BODY, 2),
    ("r9-private-use", "detail-trigger: editing \ue000 files\n", _DETAIL_BODY, 2),
    ("r9-element-question", "map-iso-42001-broad: [?a]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r9-element-colon", "map-iso-42001-broad: [:a]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r9-element-inner-question", "map-iso-42001-broad: [a?b]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r9-element-true", "map-iso-42001-broad: [true]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r9-element-false", "map-iso-42001-broad: [False]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r9-element-integer", "map-iso-42001-broad: [7]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r9-element-ids-ok", "map-iso-42001-broad: [6.7, \"6.7\", '7', A.5.1, 6.4.2]\n" + _TRIGGER,
     _DETAIL_BODY, 0),
    ("r9-element-trailing-comma", "map-iso-42001-broad: [A.5.1,]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r9-secondary-quoted-ok", "secondary: ['INTEG', \"TRUST\"]\n" + _TRIGGER, _DETAIL_BODY, 0),
    ("r9-secondary-word", "secondary: [NULL]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r9-unknown-key", "notes: a b\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r9-text-quoted-ok", _TRIGGER + "detail-reason: \"it's the reviewer's call\"\n", _DETAIL_BODY, 0),
    ("r9-text-digit-first", _TRIGGER + "detail-reason: 2026-10-03\n", _DETAIL_BODY, 2),
    ("r9-text-trailing-nbsp", _TRIGGER + "detail-reason: text\u00a0\n", _DETAIL_BODY, 2),
    ("r9-text-punctuation-ok", _TRIGGER + "detail-reason: Reviews (a, b; c) in /x - it's done.\n",
     _DETAIL_BODY, 0),
    ("r9-pack-comment-line", "# reviewed by the platform team\n", "", 2),
    # QA round 10: a value YAML reads as another type is refused (a date or integer slug, a float-shaped
    # id whose float does not print as the id, an escape in a double-quoted id), and so is a repeated key.
    # The float ids that print as themselves (the live 6.2, 6.7, 8.1 and 10.2, and 0.0001) are accepted.
    ("r10-slug-date", "", "", 2),
    ("r10-slug-digits", "", "", 2),
    ("r10-element-trailing-zero", "map-iso-42001-broad: [6.70]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r10-element-leading-zero", "map-iso-42001-broad: [06.7]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r10-element-whole-float", "map-iso-42001-broad: [6.0]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r10-element-float-exponent", "map-iso-42001-broad: [0.00001]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r10-element-float-exponent-6", "map-iso-42001-broad: [0.000001]\n" + _TRIGGER, _DETAIL_BODY, 2),
    ("r10-element-floats-ok", "map-iso-42001-broad: [6.2, 6.7, 8.1, 10.2, 0.0001]\n" + _TRIGGER,
     _DETAIL_BODY, 0),
    ("r10-element-quoted-escape", 'map-iso-42001-broad: ["a\x5cx41"]\n' + _TRIGGER, _DETAIL_BODY, 2),
    ("r10-duplicate-key", _TRIGGER + "detail-trigger: writing another fixture\n", _DETAIL_BODY, 2),
    # The BODY GRAMMAR: one case per refused shape (each exits 2); a grammar-title case sets its title
    # through _CASE_FRAME.
    ("grammar-no-title", "", "", 2),
    ("grammar-title-not-first", "", "", 2),
    ("grammar-title-no-space", "", "", 2),
    ("grammar-title-blank", "", "", 2),
    ("grammar-title-lbracket", "", "", 2),
    ("grammar-title-rbracket", "", "", 2),
    ("grammar-title-lt", "", "", 2),
    ("grammar-title-gt", "", "", 2),
    ("grammar-title-amp", "", "", 2),
    ("grammar-title-backslash", "", "", 2),
    ("grammar-title-tilde", "", "", 2),
    ("grammar-title-star", "", "", 2),
    ("grammar-title-underscore", "", "", 2),
    ("grammar-title-backtick", "", "", 2),
    ("grammar-title-non-ascii", "", "", 2),
    ("grammar-title-detail", "", "", 2),
    ("grammar-second-title", "", "\n# Second title\n", 2),
    ("grammar-start-space", "", "\n Text.\n", 2),
    ("grammar-start-tab", "", "\n\tText.\n", 2),
    ("grammar-start-gt", "", "\n> Quoted.\n", 2),
    ("grammar-start-dash", "", "\n-x\n", 2),
    ("grammar-start-plus", "", "\n+x\n", 2),
    ("grammar-start-star", "", "\n*x*\n", 2),
    ("grammar-start-equals", "", "\n=x\n", 2),
    ("grammar-start-tilde", "", "\n~x\n", 2),
    ("grammar-start-pipe", "", "\n| a | b |\n", 2),
    ("grammar-start-lt", "", "\n<x\n", 2),
    ("grammar-start-lbracket", "", "\n[x\n", 2),
    ("grammar-start-bang", "", "\n!x\n", 2),
    ("grammar-start-underscore", "", "\n___\n", 2),
    ("grammar-start-colon", "", "\n:-- | --:\n", 2),
    ("grammar-start-fence", "", "\n```\n", 2),
    ("grammar-ordered-dot", "", "\n1. Item.\n", 2),
    ("grammar-ordered-paren", "", "\n2) Item.\n", 2),
    ("grammar-ordered-bare", "", "\n3.\n", 2),
    ("grammar-lt", "", "\nText a < b.\n", 2),
    ("grammar-lbracket", "", "\nText [a.\n", 2),
    ("grammar-rbracket", "", "\nText a].\n", 2),
    ("grammar-backslash", "", "\nText a \x5c b.\n", 2),
    ("grammar-tab", "", "\nText\ta.\n", 2),
    ("grammar-non-ascii", "", "\nCaf\u00e9.\n", 2),
)
_CASE_FRAME = {
    "security-detail": {"family": "family: security\nfacet: SECI\n"},
    "empty-core": {"core": ""},
    "comment-only-core": {"core": "<!-- no visible core -->\n"},
    "r5-detail-reference-definition": {"core": "# Gen-rules detail self-test rule\n\nSee [the policy].\n"},
    "r5-paren-heading": {"core": "# The principle (highest precedence)\n\nCore text.\n"},
    "r5-paren-heading-split": {"core": "# The principle (highest precedence)\n\nCore text.\n"},
    "grammar-title-not-first": {"core": "Core text.\n\n# Gen-rules detail self-test rule\n\nCore text.\n"},
    "grammar-title-no-space": {"core": "#Title\n\nCore text.\n"},
    "grammar-title-blank": {"core": "#  \n\nCore text.\n"},
    "grammar-title-lbracket": {"core": "# See [x\n\nCore text.\n"},
    "grammar-title-rbracket": {"core": "# See x]\n\nCore text.\n"},
    "grammar-title-lt": {"core": "# a < b\n\nCore text.\n"},
    "grammar-title-gt": {"core": "# a > b\n\nCore text.\n"},
    "grammar-title-amp": {"core": "# Fish & chips\n\nCore text.\n"},
    "grammar-title-backslash": {"core": "# a \x5c b\n\nCore text.\n"},
    "grammar-title-tilde": {"core": "# ~~Old~~\n\nCore text.\n"},
    "grammar-title-star": {"core": "# *Key*\n\nCore text.\n"},
    "grammar-title-underscore": {"core": "# _Key_\n\nCore text.\n"},
    "grammar-title-backtick": {"core": "# `key`\n\nCore text.\n"},
    "grammar-title-non-ascii": {"core": "# Caf\u00e9\n\nCore text.\n"},
    "grammar-title-detail": {"core": "# Details\n\nCore text.\n"},
    "grammar-no-title": {"core": "\n"},
    "r6-fenced-comment-refdef": {"core": "# Gen-rules detail self-test rule\n\nSee [the policy].\n"},
    "r7b-frontmatter-non-ascii-tier": {"family": "family: aiqt\ntier: \u0661\u0660\nfacet: QUALI\n"},
    "r9-tier-leading-zero": {"family": "family: aiqt\ntier: 010\nfacet: QUALI\n"},
    "r9-tier-quoted": {"family": "family: aiqt\ntier: \"10\"\nfacet: QUALI\n"},
    "r9-corpus-id-leading-zero": {"cid": "0123456"},
    "r9-corpus-id-digits-ok": {"cid": "1234567"},
    "r9-corpus-id-quoted-ok": {"cid": "'0123456'"},
    "r10-slug-date": {"slug": "2026-10-03"},
    "r10-slug-digits": {"slug": "123456"},
}
# Red on revert: each guard put back to its pre-fix form in a scratch copy of this module, loaded through
# importlib, must then turn its case to the reverted exit (0 for a guard that refuses, 2 for a shape the
# grammar admits). A guard pair given as tuples is reverted together, for a case two independent guards
# each refuse. (name, fixed text, reverted text, case, exit with the guard reverted)
_NEAR_GUARD = "if _names_detail(line):"
_BLANK_GUARD = 'if found > first and lines[found - 2] != "":'
_VIS_NORMAL = 'text = unicodedata.normalize("NFKC", html.unescape(line))'
_VIS_FORMAT = 'text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")'
_VIS_DROP = 'text = _LINK_TAIL_RE.sub("", _TAG_RE.sub("", _uncontained(text)))'
_VIS_KEEP = 'return "".join(ch for ch in text if ch.isalnum()).casefold()'
_BODY_GUARD = "problem = _prose_problem(line)"
_TEXT_FIXED = '_HEADING_TEXT = frozenset(map(chr, range(0x20, 0x7f))) - frozenset("[]<>&\\\\~*_`")'
_START_FIXED = '_PROSE_START = frozenset(" \\t#>-+*=~|<[!_:")'
_CHARS_FIXED = '_PROSE_TEXT = frozenset(map(chr, range(0x20, 0x7f))) - frozenset("<[]\\\\")'
# The BODY GRAMMAR guards, each a (fixed text, reverted text) pair.
_PROSE = (_BODY_GUARD, "problem = None")
_TITLE = ("title, problem = number, _title_problem(line)", "title, problem = number, None")
_NEAR = (_NEAR_GUARD, "if False:")
_BLANK = (_BLANK_GUARD, "if False:")
_SECOND = ("if found is not None:", "if False:")
_NOTITLE = ("    if title is None:\n        raise", "    if False:\n        raise")
_FENCE = (' or line.startswith("```")', "")
# The FRONTMATTER grammar guards (D-392-FRONTMATTER-TYPED-GRAMMAR), each a (fixed text, reverted text) pair.
_FMTYPE = ("    ok, value = _scalar(kind, v)\n", "    ok, value = True, v\n")
_FMELEM = ("            ok, elem = _scalar(element, part.strip(\" \"))\n",
           "            ok, elem = True, part.strip(\" \")\n")
_FMRAW = ("for ch in scanned):", 'for ch in ""):')
_FMJOIN = (" and ch not in _JOINERS for ch in scanned", " for ch in scanned")
_VCAT_FIXED = '_LINE_CATEGORIES = frozenset(("Cc", "Cf", "Cn", "Co", "Cs", "Zl", "Zp"))'
_FMCOLON = ('if val[:1] != " ":', "if False:")
_FMINDENT = ('if raw.startswith(" "):', "if False:")
_FMHASH = ("        if comment:\n            if adopter:", "        if comment:\n            if True:")
_FMTIER = ("_TIER_RE = re.compile(r'10|20|30|40')", "_TIER_RE = re.compile(r'0*(?:10|20|30|40)')")
_FMCID = ("_CID_BARE_RE = re.compile(r'[a-z][a-z0-9]{5,}|[1-9][0-9]{5,}')",
          "_CID_BARE_RE = re.compile(r'[a-z][a-z0-9]{5,}|[0-9]{6,}')")
_FMSLUG = ('"slug": lambda t: SLUG_RE.match(t) and t[0].isalpha(),', '"slug": lambda t: SLUG_RE.match(t),')
_FMREPR = (" and repr(float(tok)) == tok:", ":")
_FMFLOAT_FIXED = "_MAP_FLOAT_RE = re.compile(r'(?:0|[1-9][0-9]{0,3})\\.[0-9]{0,5}[1-9]')"
_FMFLOAT_TAIL = (_FMFLOAT_FIXED, _FMFLOAT_FIXED.replace("[0-9]{0,5}[1-9]", "[0-9]{0,6}"))
_FMFLOAT_HEAD = (_FMFLOAT_FIXED, _FMFLOAT_FIXED.replace("(?:0|[1-9][0-9]{0,3})", "[0-9]{1,4}"))
_FMDOTTED = ("|[0-9]+(?:\\.[0-9]+){2,}')", "|[0-9]+(?:\\.[0-9]+){1,}')")
_FMQUOTED = ('"ids": _MAP_QUOTED_RE.fullmatch}', '"ids": bool}')
_FMDUP = ("if key in fm:", "if False:")
_FMTEXT = ("ch in _TEXT_PUNCT or ch in _JOINERS or unicodedata.category(ch) in _TEXT_CATEGORIES",
           'ch in _TEXT_PUNCT or ch in _JOINERS or (ch.isascii() and ch.isalnum()) or ch == " "')


def _cat(category):
    """The guard pair that drops one category from _LINE_CATEGORIES."""
    return _VCAT_FIXED, _VCAT_FIXED.replace('"{}", '.format(category), "").replace(', "{}"'.format(category), "")


_ORDERED = ("if _ORDERED_RE.match(line):", "if False:")
_CHARS = ("if not set(line) <= _PROSE_TEXT:", "if False:")
_TSTART = ('if not line.startswith("# "):', "if False:")
_TTEXT = ("if not line[2:].strip() or not set(line[2:]) <= _HEADING_TEXT:", "if False:")
_TEXT_WIDE = (_TEXT_FIXED, _TEXT_FIXED.replace("0x7f", "0x110000"))


def _without(fixed, ch):
    """The guard pair that drops ch from the quoted character set of the constant line fixed."""
    at = fixed.index('("')
    return fixed, fixed[:at] + fixed[at:].replace(ch, "", 1)


def _revert(label, case, *guards):
    """A revert entry for case that removes each of the given guard pairs together (reverted exit 0)."""
    return label, tuple(g[0] for g in guards), tuple(g[1] for g in guards), case, 0


_DETAIL_REVERTS = (
    ("missing-trigger", 'if "detail-trigger" not in fm:', "if False:", "detail-without-trigger", 0),
    ("orphan-keys", "        if present:\n", "        if False:\n", "trigger-without-detail", 0),
    ("second-heading", "if found is not None:", "if False:", "two-detail-headings", 0),
    _revert("cr-byte", "cr-byte", ('if b"\\r" in raw:', "if False:"), _CHARS),  # a CR is also not printable ASCII
    ("empty-layer", 'if not "\\n".join(lines[heading:]).strip():', "if False:",
     "empty-detail", 0),
    ("non-string-value", ("if not isinstance(fm[key], str) or not fm[key].strip():", _FMTYPE[0]),
     ("if False:", '    ok, value = (True, [v]) if kind == "text" else _scalar(kind, v)\n'), "non-string-trigger", 0),
    ("corpus-wiring", "check_detail(read_rule_source(src), fm, src.name)", "pass", "detail-without-trigger", 0),
    ("security-detail-keys", '_check_keys(fm, BASE_KEYS | {"facet", "secondary"} | MAP_KEYS | DETAIL_KEYS, name)',
     '_check_keys(fm, BASE_KEYS | {"facet", "secondary"} | MAP_KEYS, name)', "security-detail", 2),
    ("near-miss-plural", '_DETAIL_NAMES = {"detail", "details"}', '_DETAIL_NAMES = {"detail"}',
     "near-miss-plural", 0),
    ("visible-casefold", _VIS_KEEP, 'return "".join(ch for ch in text if ch.isalnum())', "near-miss-uppercase", 0),
    ("visible-whitespace", _VIS_KEEP, 'return "".join(ch for ch in text if ch.isalnum() or ch.isspace()).casefold()',
     "near-miss-two-spaces", 0),
    ("visible-indent", _VIS_KEEP, 'return "".join(ch for ch in text if ch.isalnum() or ch.isspace()).casefold()',
     "indented-heading", 0),
    ("visible-punctuation", _VIS_KEEP, 'return "".join(ch for ch in text if not ch.isspace()).casefold()',
     "near-miss-colon", 0),
    ("visible-blockquote", _VIS_KEEP, 'return "".join(ch for ch in text if not ch.isspace()).casefold()',
     "blockquote-heading", 0),
    ("visible-list", _VIS_KEEP, 'return "".join(ch for ch in text if not ch.isspace()).casefold()',
     "list-item-heading", 0),
    ("visible-bold", _VIS_KEEP, 'return "".join(ch for ch in text if not ch.isspace()).casefold()',
     "bold-near-miss", 0),
    ("visible-underscore", _VIS_KEEP, 'return "".join(ch for ch in text if not ch.isspace()).casefold()',
     "underscore-near-miss", 0),
    ("visible-backtick", _VIS_KEEP, 'return "".join(ch for ch in text if not ch.isspace()).casefold()',
     "backtick-near-miss", 0),
    ("visible-escape", _VIS_KEEP, 'return "".join(ch for ch in text if not ch.isspace()).casefold()',
     "escape-heading", 0),
    ("visible-strikethrough", _VIS_KEEP, 'return "".join(ch for ch in text if not ch.isspace()).casefold()',
     "strikethrough-heading", 0),
    ("visible-nfkc", _VIS_NORMAL, "text = html.unescape(line)", "fullwidth-heading", 0),
    ("visible-entity", _VIS_NORMAL, 'text = unicodedata.normalize("NFKC", line)', "entity-heading", 0),
    ("visible-tag", _VIS_DROP, 'text = _LINK_TAIL_RE.sub("", _uncontained(text))', "inline-html-heading", 0),
    ("visible-html-heading", _VIS_DROP, 'text = _LINK_TAIL_RE.sub("", _uncontained(text))',
     "html-heading-near-miss", 0),
    ("visible-link", _VIS_DROP, 'text = _TAG_RE.sub("", _uncontained(text))', "link-heading", 0),
    ("visible-link-setext", _VIS_DROP, 'text = _TAG_RE.sub("", _uncontained(text))', "link-setext", 0),
    ("r4-comment-reopen-link", _VIS_DROP, 'text = _TAG_RE.sub("", _uncontained(text))', "r4-comment-reopen-link", 0),
    ("visible-container", _VIS_DROP, 'text = _LINK_TAIL_RE.sub("", _TAG_RE.sub("", text))', "ordered-list-heading", 0),
    ("r4-wide-ordered-setext", _VIS_DROP, 'text = _LINK_TAIL_RE.sub("", _TAG_RE.sub("", text))',
     "r4-wide-ordered-setext", 0),
    ("r4-wide-one-setext", _VIS_DROP, 'text = _LINK_TAIL_RE.sub("", _TAG_RE.sub("", text))', "r4-wide-one-setext", 0),
    ("visible-format-marker", _VIS_FORMAT, "text = text", "invisible-before-marker", 0),
    ("r4-zwsp-lead", (_VIS_FORMAT, _VIS_KEEP), ("text = text", _VIS_KEEP.replace(
        "ch.isalnum()", 'ch.isalnum() or unicodedata.category(ch) == "Cf"')), "r4-zwsp-lead", 0),
    ("r4-zwsp-inner", (_VIS_FORMAT, _VIS_KEEP), ("text = text", _VIS_KEEP.replace(
        "ch.isalnum()", 'ch.isalnum() or unicodedata.category(ch) == "Cf"')), "r4-zwsp-inner", 0),
    ("r4-soft-hyphen", (_VIS_FORMAT, _VIS_KEEP), ("text = text", _VIS_KEEP.replace(
        "ch.isalnum()", 'ch.isalnum() or unicodedata.category(ch) == "Cf"')), "r4-soft-hyphen", 0),
    ("blank-before", _BLANK_GUARD, "if False:", "plain-blank-before", 0),
    ("r5-paren-heading", _TEXT_FIXED, _TEXT_FIXED.replace("[]", "[]()"), "r5-paren-heading", 2),
    ("r5-paren-heading-split", _TEXT_FIXED, _TEXT_FIXED.replace("[]", "[]()"), "r5-paren-heading-split", 2),
    ("prose-backtick-start", _FENCE[0], ' or line.startswith("`")', "plain-prefix-ok", 2),
    ("prose-tilde-inside", _FENCE[0], ' or "~~~" in line', "plain-tilde-marker", 2),
    # Each refusal case below goes to exit 0 with the guards that refuse it removed: the BODY GRAMMAR
    # (D-392-ENUMERATED-BODY-GRAMMAR) refuses every round-1 to round-6 reproduction.
    _revert("body-fenced-code-detail", "fenced-code-detail", _PROSE),
    _revert("body-closed-fence-and-comment", "closed-fence-and-comment", _PROSE),
    _revert("body-near-miss-heading", "near-miss-heading", _PROSE, _NEAR),
    _revert("body-near-miss-lowercase", "near-miss-lowercase", _PROSE, _NEAR),
    _revert("body-near-miss-uppercase", "near-miss-uppercase", _PROSE, _NEAR),
    _revert("body-near-miss-two-spaces", "near-miss-two-spaces", _PROSE, _NEAR),
    _revert("body-near-miss-colon", "near-miss-colon", _PROSE, _NEAR),
    _revert("body-near-miss-plural", "near-miss-plural", _PROSE, _NEAR),
    _revert("body-indented-heading", "indented-heading", _PROSE, _NEAR),
    _revert("body-blockquote-heading", "blockquote-heading", _PROSE, _NEAR),
    _revert("body-list-item-heading", "list-item-heading", _PROSE, _NEAR),
    _revert("body-ordered-list-heading", "ordered-list-heading", _PROSE, _NEAR),
    _revert("body-setext-heading", "setext-heading", _PROSE, _NEAR),
    _revert("body-setext-heading-h1", "setext-heading-h1", _PROSE, _NEAR),
    _revert("body-backtick-fenced-heading", "backtick-fenced-heading", _PROSE, _BLANK),
    _revert("body-tilde-fenced-heading", "tilde-fenced-heading", _PROSE, _BLANK),
    _revert("body-indented-fence-close", "indented-fence-close", _PROSE),
    _revert("body-fenced-near-miss", "fenced-near-miss", _PROSE, _NEAR),
    _revert("body-comment-heading", "comment-heading", _PROSE, _BLANK),
    _revert("body-comment-near-miss", "comment-near-miss", _PROSE, _NEAR),
    _revert("body-unterminated-fence", "unterminated-fence", _PROSE),
    _revert("body-unterminated-comment", "unterminated-comment", _PROSE),
    _revert("body-no-split-unterminated-fence", "no-split-unterminated-fence", _PROSE),
    _revert("body-comment-only-core", "comment-only-core", _TITLE),
    _revert("body-comment-only-detail", "comment-only-detail", _PROSE),
    _revert("body-empty-comment-detail", "empty-comment-detail", _PROSE),
    _revert("body-longer-fence-close", "longer-fence-close", _PROSE, _BLANK),
    _revert("body-mixed-fence-close", "mixed-fence-close", _PROSE, _BLANK),
    _revert("body-backtick-info-not-fence", "backtick-info-not-fence", _PROSE),
    _revert("body-html-pre", "html-pre", _PROSE, _BLANK),
    _revert("body-html-div", "html-div", _PROSE, _BLANK),
    _revert("body-html-script", "html-script", _PROSE, _BLANK),
    _revert("body-html-details", "html-details", _PROSE),
    _revert("body-html-close-tag", "html-close-tag", _PROSE, _BLANK),
    _revert("body-html-processing", "html-processing", _PROSE, _BLANK),
    _revert("body-html-declaration", "html-declaration", _PROSE, _BLANK),
    _revert("body-html-heading-near-miss", "html-heading-near-miss", _PROSE, _NEAR),
    _revert("body-blockquote-setext", "blockquote-setext", _PROSE, _NEAR),
    _revert("body-list-setext", "list-setext", _PROSE, _NEAR),
    _revert("body-multiline-setext", "multiline-setext", _PROSE, _NEAR),
    _revert("body-bold-setext", "bold-setext", _PROSE, _NEAR),
    _revert("body-bold-near-miss", "bold-near-miss", _PROSE, _NEAR),
    _revert("body-underscore-near-miss", "underscore-near-miss", _PROSE, _NEAR),
    _revert("body-backtick-near-miss", "backtick-near-miss", _PROSE, _NEAR),
    _revert("body-code-span-comment-opener", "code-span-comment-opener", _PROSE),
    _revert("body-empty-comment", "empty-comment", _PROSE),
    _revert("body-link-heading", "link-heading", _PROSE, _NEAR),
    _revert("body-entity-heading", "entity-heading", _PROSE, _NEAR),
    _revert("body-inline-html-heading", "inline-html-heading", _PROSE, _NEAR),
    _revert("body-escape-heading", "escape-heading", _PROSE, _NEAR),
    _revert("body-strikethrough-heading", "strikethrough-heading", _PROSE, _NEAR),
    _revert("body-link-setext", "link-setext", _PROSE, _NEAR),
    _revert("body-fullwidth-heading", "fullwidth-heading", _PROSE, _NEAR),
    _revert("body-invisible-before-marker", "invisible-before-marker", _PROSE, _NEAR),
    _revert("body-plain-setext", "plain-setext", _PROSE),
    _revert("body-no-split-setext", "no-split-setext", _PROSE),
    _revert("body-heading-then-rule", "heading-then-rule", _PROSE),
    _revert("body-blank-then-rule", "blank-then-rule", _PROSE),
    _revert("body-fence-then-rule", "fence-then-rule", _PROSE),
    _revert("body-comment-then-rule", "comment-then-rule", _PROSE),
    _revert("body-rule-then-rule", "rule-then-rule", _PROSE),
    _revert("body-list-fence", "list-fence", _PROSE, _BLANK),
    _revert("body-list-fence-comment", "list-fence-comment", _PROSE, _BLANK),
    _revert("body-list-comment", "list-comment", _PROSE, _BLANK),
    _revert("body-blockquote-fence", "blockquote-fence", _PROSE),
    _revert("body-indented-fence", "indented-fence", _PROSE),
    _revert("body-indented-comment", "indented-comment", _PROSE),
    _revert("body-r4-setext-dash-space", "r4-setext-dash-space", _PROSE, _NEAR),
    _revert("body-r4-setext-dash-tab", "r4-setext-dash-tab", _PROSE, _NEAR),
    _revert("body-r4-quote-setext-dash", "r4-quote-setext-dash", _PROSE, _NEAR),
    _revert("body-r4-list-setext-dash", "r4-list-setext-dash", _PROSE, _NEAR),
    _revert("body-r4-quoted-script", "r4-quoted-script", _PROSE, _BLANK),
    _revert("body-r4-quoted-script-blank", "r4-quoted-script-blank", _PROSE),
    _revert("body-r4-comment-reopen", "r4-comment-reopen", _PROSE, _BLANK),
    _revert("body-r4-comment-reopen-next", "r4-comment-reopen-next", _PROSE, _BLANK),
    _revert("body-r4-comment-reopen-blank", "r4-comment-reopen-blank", _PROSE),
    _revert("body-r4-comment-reopen-setext", "r4-comment-reopen-setext", _PROSE, _NEAR),
    _revert("body-r4-comment-reopen-link", "r4-comment-reopen-link", _PROSE, _NEAR),
    _revert("body-r4-wide-ordered-setext", "r4-wide-ordered-setext", _PROSE, _NEAR),
    _revert("body-r4-wide-ordered-setext-h1", "r4-wide-ordered-setext-h1", _PROSE, _NEAR),
    _revert("body-r4-wide-bullet-setext", "r4-wide-bullet-setext", _PROSE, _NEAR),
    _revert("body-r4-wide-one-setext", "r4-wide-one-setext", _PROSE, _NEAR),
    _revert("body-r4-nested-list-setext", "r4-nested-list-setext", _PROSE, _NEAR),
    _revert("body-r4-tab-list-setext", "r4-tab-list-setext", _PROSE, _NEAR),
    _revert("body-r4-zwsp-lead", "r4-zwsp-lead", _PROSE, _NEAR),
    _revert("body-r4-zwsp-inner", "r4-zwsp-inner", _PROSE, _NEAR),
    _revert("body-r4-soft-hyphen", "r4-soft-hyphen", _PROSE, _NEAR),
    _revert("body-r5-balanced-link-heading", "r5-balanced-link-heading", _PROSE),
    _revert("body-r5-comment-gt-heading", "r5-comment-gt-heading", _PROSE),
    _revert("body-r5-multiline-html-heading", "r5-multiline-html-heading", _PROSE),
    _revert("body-r5-multiline-comment-setext", "r5-multiline-comment-setext", _PROSE),
    _revert("body-r5-multiline-span-setext", "r5-multiline-span-setext", _PROSE),
    _revert("body-r5-hangul-filler", "r5-hangul-filler", _PROSE),
    _revert("body-r5-halfwidth-hangul-filler", "r5-halfwidth-hangul-filler", _PROSE),
    _revert("body-r5-cyrillic-e", "r5-cyrillic-e", _PROSE),
    _revert("body-r5-detail-reference-definition", "r5-detail-reference-definition", _PROSE),
    _revert("body-r5-paren-subheading", "r5-paren-subheading", _PROSE),
    _revert("body-plain-list-ok", "plain-list-ok", _PROSE),
    _revert("body-plain-subheading", "plain-subheading", _PROSE),
    _revert("body-plain-start-plus", "plain-start-plus", _PROSE),
    _revert("body-plain-start-quote", "plain-start-quote", _PROSE),
    _revert("body-plain-start-table", "plain-start-table", _PROSE),
    _revert("body-plain-start-rule", "plain-start-rule", _PROSE),
    _revert("body-plain-heading-seven", "plain-heading-seven", _PROSE),
    _revert("body-plain-tab", "plain-tab", _PROSE),
    _revert("body-plain-lt", "plain-lt", _PROSE),
    _revert("body-plain-fence-marker", "plain-fence-marker", _PROSE),
    _revert("body-plain-list-paren", "plain-list-paren", _PROSE),
    _revert("body-plain-nested-ordered", "plain-nested-ordered", _PROSE),
    _revert("body-plain-heading-empty", "plain-heading-empty", _PROSE),
    _revert("body-plain-heading-bracket", "plain-heading-bracket", _PROSE),
    _revert("body-plain-heading-lt", "plain-heading-lt", _PROSE),
    _revert("body-plain-heading-amp", "plain-heading-amp", _PROSE),
    _revert("body-plain-heading-backslash", "plain-heading-backslash", _PROSE),
    _revert("body-plain-heading-tilde", "plain-heading-tilde", _PROSE),
    _revert("body-plain-heading-star", "plain-heading-star", _PROSE),
    _revert("body-plain-heading-underscore", "plain-heading-underscore", _PROSE),
    _revert("body-plain-heading-backtick", "plain-heading-backtick", _PROSE),
    _revert("body-plain-heading-tab", "plain-heading-tab", _PROSE),
    _revert("body-plain-indented-code", "plain-indented-code", _PROSE),
    _revert("body-plain-item-indent", "plain-item-indent", _PROSE),
    _revert("body-plain-item-text", "plain-item-text", _PROSE),
    _revert("body-r6-fenced-comment", "r6-fenced-comment", _PROSE),
    _revert("body-r6-fenced-comment-split", "r6-fenced-comment-split", _PROSE),
    _revert("body-r6-fenced-comment-refdef", "r6-fenced-comment-refdef", _PROSE),
    _revert("body-r6-fenced-hangul", "r6-fenced-hangul", _PROSE),
    _revert("body-r6-ordered-setext", "r6-ordered-setext", _PROSE),
    _revert("body-r6-ordered-setext-split", "r6-ordered-setext-split", _PROSE),
    _revert("body-r6-ordered-break-setext", "r6-ordered-break-setext", _PROSE),
    _revert("body-r6-ordered-fence-setext", "r6-ordered-fence-setext", _PROSE),
    _revert("body-r6-ordered-paren-setext", "r6-ordered-paren-setext", _PROSE),
    _revert("fm-r6-frontmatter-heading", "r6-frontmatter-heading", _FMHASH),
    _revert("fm-r6-frontmatter-indented-comment", "r6-frontmatter-indented-comment", _FMHASH),
    _revert("fm-r9-pack-comment-line", "r9-pack-comment-line", _FMHASH),
    _revert("fm-r7-frontmatter-html-heading", "r7-frontmatter-html-heading", _FMTYPE),
    _revert("fm-r7-frontmatter-style", "r7-frontmatter-style", _FMTYPE),
    _revert("fm-r7-frontmatter-quoted-html", "r7-frontmatter-quoted-html", _FMTYPE),
    _revert("fm-r7-frontmatter-link", "r7-frontmatter-link", _FMTYPE),
    _revert("fm-r7-frontmatter-backslash", "r7-frontmatter-backslash", _FMTYPE),
    ("fm-r7b-ascii-only", _FMTEXT[0], _FMTEXT[1], "r7-frontmatter-non-ascii", 2),
    _revert("fm-r7b-control", "r7b-frontmatter-tab", _cat("Cc"), _FMTYPE),
    _revert("fm-r7b-format", "r7b-frontmatter-format", _cat("Cf"), _FMTYPE),
    _revert("fm-r7b-line-separator", "r7b-frontmatter-line-separator", _cat("Zl"), _FMTYPE),
    _revert("fm-r7b-paragraph-separator", "r7b-frontmatter-paragraph-separator", _cat("Zp"), _FMTYPE),
    _revert("fm-r9-noncharacter-fffe", "r9-noncharacter-fffe", _cat("Cn"), _FMTYPE),
    _revert("fm-r9-noncharacter-ffff", "r9-noncharacter-ffff", _cat("Cn"), _FMTYPE),
    _revert("fm-r9-private-use", "r9-private-use", _cat("Co"), _FMTYPE),
    _revert("fm-r8-f1-paragraph-separator-slug", "r8-f1-paragraph-separator-slug", _FMRAW, _FMCOLON, _FMTYPE),
    _revert("fm-r8-f1-next-line-key", "r8-f1-next-line-key", _FMRAW, _FMCOLON, _FMTYPE),
    _revert("fm-r8-codex-line-separator-key", "r8-codex-line-separator-key", _FMRAW, _FMCOLON, _FMTYPE),
    _revert("fm-r8-raw-leading-next-line", "r8-raw-leading-next-line", _FMRAW, _FMTYPE),
    _revert("fm-r8-raw-trailing-separator", "r8-raw-trailing-separator", _FMRAW, _FMTYPE),
    _revert("fm-r8-raw-trailing-line-separator", "r8-raw-trailing-line-separator", _FMRAW, _FMTYPE),
    ("fm-r8-zwnj-persian", _FMJOIN[0], _FMJOIN[1], "r8-zwnj-persian", 2),
    ("fm-r9-zwj-malayalam", _FMJOIN[0], _FMJOIN[1], "r9-zwj-malayalam", 2),
    _revert("fm-r8-zwj-emoji", "r8-zwj-emoji", _FMTYPE),
    _revert("fm-r8-null-tilde", "r8-null-tilde", _FMTYPE),
    _revert("fm-r8-null-lower", "r8-null-lower", _FMTYPE),
    _revert("fm-r8-null-title", "r8-null-title", _FMTYPE),
    _revert("fm-r8-null-upper", "r8-null-upper", _FMTYPE),
    _revert("fm-r8-bool-yes", "r8-bool-yes", _FMTYPE),
    _revert("fm-r8-value-equals", "r8-value-equals", _FMTYPE),
    _revert("fm-r8-comment", "r8-comment", _FMTYPE),
    _revert("fm-r8-start-folded", "r8-start-folded", _FMTYPE),
    _revert("fm-r8-start-literal", "r8-start-literal", _FMTYPE),
    _revert("fm-r8-start-anchor", "r8-start-anchor", _FMTYPE),
    _revert("fm-r8-start-alias", "r8-start-alias", _FMTYPE),
    _revert("fm-r8-start-tag", "r8-start-tag", _FMTYPE),
    _revert("fm-r8-start-percent", "r8-start-percent", _FMTYPE),
    _revert("fm-r8-start-at", "r8-start-at", _FMTYPE),
    _revert("fm-r8-start-backtick", "r8-start-backtick", _FMTYPE),
    _revert("fm-r8-start-brace", "r8-start-brace", _FMTYPE),
    _revert("fm-r8-start-comma", "r8-start-comma", _FMTYPE),
    _revert("fm-r8-start-question", "r8-start-question", _FMTYPE),
    _revert("fm-r8-start-dash", "r8-start-dash", _FMTYPE),
    _revert("fm-r8-dash-word", "r8-dash-word", _FMTYPE),
    _revert("fm-r8-quote-unclosed", "r8-quote-unclosed", _FMTYPE),
    _revert("fm-r8-quote-doubled", "r8-quote-doubled", _FMTYPE),
    _revert("fm-r8-mapping-separator", "r8-mapping-separator", _FMTYPE),
    _revert("fm-r8-final-colon", "r8-final-colon", _FMTYPE),
    _revert("fm-r8-url", "r8-url", _FMTYPE),
    _revert("fm-r8-no-space-after-colon", "r8-no-space-after-colon", _FMCOLON),
    _revert("fm-r8-indented-under-folded", "r8-indented-under-folded", _FMTYPE, _FMINDENT),
    _revert("fm-r8-indented-under-value", "r8-indented-under-value", _FMINDENT),
    _revert("fm-r8-element-null", "r8-element-null", _FMELEM),
    _revert("fm-r8-element-pair", "r8-element-pair", _FMELEM),
    _revert("fm-r8-element-brace", "r8-element-brace", _FMELEM),
    _revert("fm-r9-tier-leading-zero", "r9-tier-leading-zero", _FMTIER),
    _revert("fm-r7b-ascii-int", "r7b-frontmatter-non-ascii-tier", (_FMTIER[0], "_TIER_RE = re.compile(r'\\d\\d')")),
    _revert("fm-r7b-lf-split", "r7b-frontmatter-separator-key",
            ('for raw in text[4:end].split("\\n"):', "for raw in text[4:end].splitlines():")),
    _revert("fm-r9-tier-quoted", "r9-tier-quoted",
            ('quoted_ok = {"id": CID_RE.match,', 'quoted_ok = {"tier": _TIER_RE.fullmatch, "id": CID_RE.match,')),
    _revert("fm-r9-corpus-id-leading-zero", "r9-corpus-id-leading-zero", _FMCID),
    _revert("fm-r9-element-question", "r9-element-question", _FMELEM),
    _revert("fm-r9-element-colon", "r9-element-colon", _FMELEM),
    _revert("fm-r9-element-inner-question", "r9-element-inner-question", _FMELEM),
    _revert("fm-r9-element-true", "r9-element-true", _FMELEM),
    _revert("fm-r9-element-false", "r9-element-false", _FMELEM),
    _revert("fm-r9-element-integer", "r9-element-integer", _FMELEM),
    _revert("fm-r9-text-digit-first", "r9-text-digit-first", _FMTYPE),
    _revert("fm-r9-text-trailing-nbsp", "r9-text-trailing-nbsp", _FMTYPE),
    _revert("fm-r10-slug-date", "r10-slug-date", _FMSLUG),
    _revert("fm-r10-slug-digits", "r10-slug-digits", _FMSLUG),
    _revert("fm-r10-element-trailing-zero", "r10-element-trailing-zero", _FMFLOAT_TAIL, _FMREPR),
    _revert("fm-r10-element-trailing-zero-dotted", "r10-element-trailing-zero", _FMDOTTED),
    _revert("fm-r10-element-leading-zero", "r10-element-leading-zero", _FMFLOAT_HEAD, _FMREPR),
    _revert("fm-r10-element-leading-zero-dotted", "r10-element-leading-zero", _FMDOTTED),
    _revert("fm-r10-element-whole-float", "r10-element-whole-float", _FMFLOAT_TAIL),
    _revert("fm-r10-element-float-exponent", "r10-element-float-exponent", _FMREPR),
    _revert("fm-r10-element-float-exponent-6", "r10-element-float-exponent-6", _FMREPR),
    _revert("fm-r10-element-quoted-escape", "r10-element-quoted-escape", _FMQUOTED),
    _revert("fm-r10-duplicate-key", "r10-duplicate-key", _FMDUP),
    _revert("body-grammar-no-title", "grammar-no-title", _NOTITLE),
    _revert("body-grammar-title-not-first", "grammar-title-not-first", _TITLE, _PROSE),
    _revert("body-grammar-title-no-space", "grammar-title-no-space", _TSTART),
    _revert("body-grammar-title-blank", "grammar-title-blank", _TTEXT),
    _revert("body-grammar-title-lbracket", "grammar-title-lbracket", _without(_TEXT_FIXED, "[")),
    _revert("body-grammar-title-rbracket", "grammar-title-rbracket", _without(_TEXT_FIXED, "]")),
    _revert("body-grammar-title-lt", "grammar-title-lt", _without(_TEXT_FIXED, "<")),
    _revert("body-grammar-title-gt", "grammar-title-gt", _without(_TEXT_FIXED, ">")),
    _revert("body-grammar-title-amp", "grammar-title-amp", _without(_TEXT_FIXED, "&")),
    _revert("body-grammar-title-backslash", "grammar-title-backslash", _without(_TEXT_FIXED, "\\\\")),
    _revert("body-grammar-title-tilde", "grammar-title-tilde", _without(_TEXT_FIXED, "~")),
    _revert("body-grammar-title-star", "grammar-title-star", _without(_TEXT_FIXED, "*")),
    _revert("body-grammar-title-underscore", "grammar-title-underscore", _without(_TEXT_FIXED, "*_")),
    _revert("body-grammar-title-backtick", "grammar-title-backtick", _without(_TEXT_FIXED, "`")),
    _revert("body-grammar-title-non-ascii", "grammar-title-non-ascii", _TEXT_WIDE),
    _revert("body-grammar-title-detail", "grammar-title-detail", _NEAR),
    _revert("body-grammar-second-title", "grammar-second-title", _without(_START_FIXED, "#")),
    _revert("body-grammar-start-space", "grammar-start-space", _without(_START_FIXED, " ")),
    _revert("body-grammar-start-tab", "grammar-start-tab", _without(_START_FIXED, "\\t"), _CHARS),
    _revert("body-grammar-start-gt", "grammar-start-gt", _without(_START_FIXED, ">")),
    _revert("body-grammar-start-dash", "grammar-start-dash", _without(_START_FIXED, "-")),
    _revert("body-grammar-start-plus", "grammar-start-plus", _without(_START_FIXED, "+")),
    _revert("body-grammar-start-star", "grammar-start-star", _without(_START_FIXED, "*")),
    _revert("body-grammar-start-equals", "grammar-start-equals", _without(_START_FIXED, "=")),
    _revert("body-grammar-start-tilde", "grammar-start-tilde", _without(_START_FIXED, "~")),
    _revert("body-grammar-start-pipe", "grammar-start-pipe", _without(_START_FIXED, "|")),
    _revert("body-grammar-start-lt", "grammar-start-lt", _without(_START_FIXED, "<"), _without(_CHARS_FIXED, "<")),
    _revert("body-grammar-start-lbracket", "grammar-start-lbracket", _without(_START_FIXED, "["), _without(_CHARS_FIXED, "[")),
    _revert("body-grammar-start-bang", "grammar-start-bang", _without(_START_FIXED, "!")),
    _revert("body-grammar-start-underscore", "grammar-start-underscore", _without(_START_FIXED, "_")),
    _revert("body-grammar-start-colon", "grammar-start-colon", _without(_START_FIXED, ":")),
    _revert("body-grammar-start-fence", "grammar-start-fence", _FENCE),
    _revert("body-grammar-ordered-dot", "grammar-ordered-dot", _ORDERED),
    _revert("body-grammar-ordered-paren", "grammar-ordered-paren", _ORDERED),
    _revert("body-grammar-ordered-bare", "grammar-ordered-bare", _ORDERED),
    _revert("body-grammar-lt", "grammar-lt", _without(_CHARS_FIXED, "<")),
    _revert("body-grammar-lbracket", "grammar-lbracket", _without(_CHARS_FIXED, "[")),
    _revert("body-grammar-rbracket", "grammar-rbracket", _without(_CHARS_FIXED, "]")),
    _revert("body-grammar-backslash", "grammar-backslash", _without(_CHARS_FIXED, "\\\\")),
    _revert("body-grammar-tab", "grammar-tab", _CHARS),
    _revert("body-grammar-non-ascii", "grammar-non-ascii", _CHARS),
)
# Each of these cases is also refused by the BODY GRAMMAR, so its revert removes the prose line check
# together with the visible-text step it names.
_ALSO_BODY = frozenset(("near-miss-plural", "visible-casefold", "visible-whitespace", "visible-indent", "visible-punctuation", "visible-blockquote", "visible-list", "visible-bold", "visible-underscore", "visible-backtick", "visible-escape", "visible-strikethrough", "visible-nfkc", "visible-entity", "visible-tag", "visible-html-heading", "visible-link", "visible-link-setext", "r4-comment-reopen-link", "visible-container", "r4-wide-ordered-setext", "r4-wide-one-setext", "visible-format-marker", "r4-zwsp-lead", "r4-zwsp-inner", "r4-soft-hyphen"))


def _with_body(revert):
    """The revert with the BODY GRAMMAR prose line check reverted as well."""
    label, old, new, case, reverted_exit = revert
    old, new = (old if isinstance(old, tuple) else (old,)), (new if isinstance(new, tuple) else (new,))
    return label, old + (_BODY_GUARD,), new + ("problem = None",), case, reverted_exit


_DETAIL_REVERTS = tuple(_with_body(r) if r[0] in _ALSO_BODY else r for r in _DETAIL_REVERTS)
# Cases a later check refuses even with the frontmatter guard reverted (a missing trigger, an empty
# trigger), so their revert is checked at parse_source: the fixed parse refuses the source and the
# reverted parse reads it. (name, fixed texts, reverted texts, case)
_PARSE_REVERTS = (
    ("fm-r8-f1-line-separator-trigger",) + _revert("", "", _FMRAW, _FMCOLON, _FMTYPE)[1:3]
    + ("r8-f1-line-separator-trigger",),
    ("fm-r8-indented-under-empty",) + _revert("", "", _FMCOLON, _FMINDENT, _FMTYPE)[1:3]
    + ("r8-indented-under-empty",),
)


def _detail_case_root(base, name):
    """A synthetic corpus holding the one rule of the named detail case. Returns the case root."""
    _case, keys, body, _expected = next(c for c in _DETAIL_CASES if c[0] == name)
    frame = dict(dict(family=_AIQT_FAMILY, core=_CORE, cid="selfd1", slug="gen-rules-selftest-detail"),
                 **_CASE_FRAME.get(name, {}))
    src = base / name / ".aiqt" / "core" / "rules"
    src.mkdir(parents=True)
    (src / "gen-rules-selftest-detail.md").write_bytes(
        _DETAIL_SRC.format(keys=keys, body=body, **frame).encode("utf-8"))
    return base / name


def _load_reverted(base, label, old, new):
    """This module with one production guard reverted, written under base and loaded through importlib
    (never exec); old and new are one text each, or equal-length tuples reverted together. The
    mutation is confined to the text above the self-test section, so the revert table
    itself is never the match. sys.path is restored after the load."""
    import importlib.util
    source = Path(__file__).read_text(encoding="utf-8")
    production, sep, tests = source.partition("\n# --- self-test ")
    pairs = list(zip(old, new)) if isinstance(old, tuple) else [(old, new)]
    for fixed, reverted in pairs:
        if not sep or production.count(fixed) != 1:
            raise AssertionError("revert {}: the fixed text must occur exactly once in the production code"
                                 .format(label))
        production = production.replace(fixed, reverted, 1)
    path = base / "gen_rules_reverted_{}.py".format(label.replace("-", "_"))
    path.write_text(production + sep + tests, encoding="utf-8")
    saved = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def run_quiet(root, check, runner=run):
        # A reverted narrow (OSError-only) arm raises UnicodeDecodeError out of run(); catch it and
        # return a non-int sentinel so it registers as a FAILURE against the expected exit code rather
        # than aborting the self-test or letting it exit early green.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return runner(root, check)
            except Exception as exc:  # noqa: BLE001  a revert surfaces here as UnicodeDecodeError
                return "raised {}".format(type(exc).__name__)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-rules-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    try:
        # A synthetic corpus (one source rule) so `desired` carries exactly one generated target, then
        # pre-write that target as invalid UTF-8 bytes so the reconcile's drift-compare read hits it.
        src = tmp / ".aiqt" / "core" / "rules"
        src.mkdir(parents=True)
        (src / "gen-rules-selftest-target.md").write_text(_RULE_SRC, encoding="utf-8")
        target = tmp / ".claude" / "rules" / _RULE_REL
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\xff\xfe not utf-8")
        if run_quiet(tmp, check=True) != 2:
            failures.append("invalid-UTF-8 generated target expected exit 2 (fail-closed)")

        # The two-layer split: a well-formed detail rule generates and then checks clean, its generated
        # file still the whole source byte for byte; every malformed layout is a malformed source (exit 2).
        detail_base = tmp / "detail"
        for name, _keys, _body, expected in _DETAIL_CASES:
            root = _detail_case_root(detail_base, name)
            got = run_quiet(root, check=False)
            if got != expected:
                failures.append("detail case {}: expected exit {}, got {!r}".format(name, expected, got))
            elif expected == 0:
                source = root / ".aiqt" / "core" / "rules" / "gen-rules-selftest-detail.md"
                generated = root / ".claude" / "rules" / derive(parse_source(source), source.name)
                if not generated.is_file() or generated.read_bytes() != source.read_bytes():
                    failures.append("detail case {}: the generated file is not the source byte for byte"
                                    .format(name))
                if run_quiet(root, check=True) != 0:
                    failures.append("detail case {}: --check after generation expected exit 0".format(name))
        revert_base = tmp / "reverted"
        revert_base.mkdir()
        for label, old, new, case, reverted_exit in _DETAIL_REVERTS:
            try:
                mutant = _load_reverted(revert_base, label, old, new)
            except AssertionError as exc:
                failures.append(str(exc))
                continue
            got = run_quiet(_detail_case_root(revert_base / label, case), check=False, runner=mutant.run)
            if got != reverted_exit:
                failures.append("revert {}: with the guard removed, case {} expected exit {} (the guard is "
                                "what decides it), got {!r}".format(label, case, reverted_exit, got))

        # The adopter-shaped rule is accepted, and is refused once the ASCII-only value check is put back.
        adopter = tmp / "adopter" / _ADOPTER_NAME
        adopter.parent.mkdir()
        adopter.write_bytes(_ADOPTER_SRC.encode("utf-8"))
        try:
            got = derive(parse_source(adopter), adopter.name, ("pack", "adopter"))
        except ValueError as exc:
            got = "refused ({})".format(exc)
        if got != "security/" + _ADOPTER_NAME:
            failures.append("adopter case: a non-ASCII string value expected acceptance, got {!r}".format(got))
        try:
            mutant = _load_reverted(revert_base, "fm-r7b-adopter-ascii-only", *_FMTEXT)
        except AssertionError as exc:
            failures.append(str(exc))
        else:
            try:
                mutant.parse_source(adopter)
                failures.append("revert fm-r7b-adopter-ascii-only: with the ASCII-only value check put back, "
                                "the adopter case expected a refusal")
            except ValueError:
                pass

        rules = tmp / "adopter-mode" / ".claude" / "rules"

        def placement(module, path):
            # The read check_rule_placement.check_drift makes: adopter mode, adopter origin allowed. None
            # when the gate accepts the rule at its path, else the refusal.
            try:
                got = module.derive(module.parse_source(path, adopter=True), path.name, ("pack", "adopter"))
                return None if got == _ADOPTER_MODE_REL else "derives {}".format(got)
            except ValueError as exc:
                return str(exc)

        this = sys.modules[__name__]
        adopter_paths = {}
        for case, content in [c[:2] for c in _ADOPTER_MODE_CASES + _ADOPTER_MODE_REFUSALS]:
            adopter_paths[case] = rules / case / _ADOPTER_MODE_REL
            adopter_paths[case].parent.mkdir(parents=True)
            adopter_paths[case].write_bytes(content.encode("utf-8"))
        for case, _content, strict_text in _ADOPTER_MODE_CASES:
            got = placement(this, adopter_paths[case])
            if got is not None:
                failures.append("adopter case {}: expected the placement gate to accept it, got {!r}".format(case, got))
            try:
                parse_source(adopter_paths[case])
                failures.append("adopter case {}: expected the pack's strict reader to refuse it".format(case))
            except ValueError as exc:
                if strict_text not in str(exc):
                    failures.append("adopter case {}: the strict refusal should say {!r}, got {}".format(
                        case, strict_text, exc))
        for case, _content, refusal_text in _ADOPTER_MODE_REFUSALS:
            got = placement(this, adopter_paths[case])
            if got is None:
                failures.append("adopter case {}: expected the placement gate to refuse it".format(case))
            elif refusal_text not in got:
                failures.append("adopter case {}: the refusal should say {!r}, got {}".format(case, refusal_text, got))
        for label, case, old, new in _ADOPTER_MODE_REVERTS:
            try:
                mutant = _load_reverted(revert_base, "adopter-" + label, old, new)
            except AssertionError as exc:
                failures.append(str(exc))
                continue
            if (placement(mutant, adopter_paths[case]) is None) != (case in {c[0] for c in _ADOPTER_MODE_REFUSALS}):
                failures.append("revert adopter-{}: with the guard changed, the placement gate's read of case "
                                "{} expected to flip".format(label, case))
        agreement = _yaml_agreement(repo_root(), tmp / "adopter-mode")
        if agreement is not None:
            failures.extend("YAML agreement: " + f for f in agreement[0][:20])
        # The accepted adopter-mode cases read alike to PyYAML in ADOPTER MODE, and the comparison goes red
        # when ADOPTER MODE is made to drop a key (here, by skipping the facet line as a comment).
        adopter_accepted = [adopter_paths[c[0]] for c in _ADOPTER_MODE_CASES]
        adopter_agreement = _yaml_adopter_agreement(adopter_accepted)
        if adopter_agreement is not None:
            failures.extend("YAML agreement (adopter mode): " + f for f in adopter_agreement)
            try:
                mutant = _load_reverted(revert_base, "adopter-yaml-drop-key", *_ADOPTER_DROP_KEY)
            except AssertionError as exc:
                failures.append(str(exc))
            else:
                dropped = mutant._yaml_adopter_agreement(adopter_accepted)
                if len([f for f in dropped if "keys differ" in f]) != len(adopter_accepted):
                    failures.append("revert adopter-yaml-drop-key: with ADOPTER MODE dropping the facet key, "
                                    "the PyYAML comparison expected a key difference for each of the {} "
                                    "adopter-mode case(s), got {!r}".format(len(adopter_accepted), dropped))

        for label, old, new, case in _PARSE_REVERTS:
            source = _detail_case_root(revert_base / label, case) / ".aiqt" / "core" / "rules" / \
                "gen-rules-selftest-detail.md"
            try:
                parse_source(source)
                failures.append("parse case {}: expected parse_source to refuse it".format(case))
            except ValueError:
                pass
            try:
                mutant = _load_reverted(revert_base, label, old, new)
            except AssertionError as exc:
                failures.append(str(exc))
                continue
            try:
                mutant.parse_source(source)
            except ValueError as exc:
                failures.append("revert {}: with the guard removed, parse_source expected to read case {}, "
                                "got {}".format(label, case, exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: an invalid-UTF-8 generated target fails closed (exit 2), not a raw "
          "UnicodeDecodeError traceback (guards the widened reconcile arm); {} detail layout case(s) "
          "hold and {} guard revert(s) each go red; an adopter-shaped rule with a non-ASCII value is accepted "
          "and goes red under the ASCII-only revert; {} parse-level revert(s) each go red; {} adopter-mode "
          "case(s) pass the placement gate and are refused by the pack reader, {} more are refused by both, "
          "and {} adopter-mode revert(s) each go red; {}.".format(
              len(_DETAIL_CASES), len(_DETAIL_REVERTS) + 1, len(_PARSE_REVERTS), len(_ADOPTER_MODE_CASES),
              len(_ADOPTER_MODE_REFUSALS), len(_ADOPTER_MODE_REVERTS),
              "PyYAML agreement NOT RUN (PyYAML is not importable)" if agreement is None else
              "PyYAML safe_load reads the same keys and values for {} live source(s), {} fuzzed "
              "frontmatter(s) ({} disclosed bare float id(s)) and {} adopter-mode case(s) read in ADOPTER "
              "MODE (red when ADOPTER MODE drops a key)".format(*agreement[1:], len(_ADOPTER_MODE_CASES))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
