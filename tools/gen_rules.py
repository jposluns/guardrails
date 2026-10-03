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
  The frontmatter carries no heading either: a frontmatter line that starts with `#` (a YAML comment,
  shown as a heading by a renderer that does not read frontmatter) is refused.
  Each of these is refused as a malformed source (exit 2, naming the file and, for a line, its number):
    - either key without the split, a missing detail-trigger with it, or a key value that is not a
      non-empty string;
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


def _unquote(tok):
    """A single scalar STRING element: strip matching quotes, else the bare token. Fail-closed."""
    if tok and tok[0] in "\"'":
        if len(tok) >= 2 and tok[-1] == tok[0]:
            return tok[1:-1]
        raise ValueError("malformed quoted value {!r}".format(tok))
    return tok


def _value(v):
    if v and v[0] in "\"'":
        return _unquote(v)
    # Flow sequence of strings, e.g. [INTEG, QUALI] (spec section 4). Elements are strings; a bare
    # element is not coerced to int/bool. Malformed (unclosed, or an empty element) is fail-closed.
    if v.startswith("["):
        if not v.endswith("]"):
            raise ValueError("malformed flow sequence {!r} (unterminated)".format(v))
        inner = v[1:-1].strip()
        if inner == "":
            return []
        if "[" in inner or "]" in inner:
            raise ValueError("nested flow sequence not allowed in {!r} (strings only)".format(v))
        elems = []
        for part in inner.split(","):
            part = part.strip()
            if part == "":
                raise ValueError("empty element in flow sequence {!r}".format(v))
            elems.append(_unquote(part))
        return elems
    if v in ("true", "false"):
        return v == "true"
    if re.fullmatch(r'-?\d+', v):
        return int(v)
    return v


def decode_rule_source(raw, name):
    """A rule source's text from its raw bytes: UTF-8 with no newline translation. Raises ValueError on
    a CR byte (LF line endings only, as check_byte_canon requires) and on invalid UTF-8 (a
    UnicodeDecodeError, itself a ValueError). check_clauses decodes rule sources through this too."""
    if b"\r" in raw:
        raise ValueError("{}: line {}: holds a CR byte; a rule source uses LF line endings only".format(
            name, raw.count(b"\n", 0, raw.index(b"\r")) + 1))
    return raw.decode("utf-8")


def read_rule_source(path):
    """Read one rule source the one way both gen_rules and check_clauses read it (see decode_rule_source)."""
    return decode_rule_source(path.read_bytes(), path.name)


def parse_source(path):
    text = read_rule_source(path)
    if not text.startswith("---\n"):
        raise ValueError("{}: no frontmatter".format(path.name))
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("{}: unterminated frontmatter".format(path.name))
    fm = {}
    for line in text[4:end].splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            raise ValueError("{}: frontmatter line {!r} starts with '#' (a YAML comment is refused: a "
                             "renderer that does not read frontmatter shows it as a heading)".format(path.name, line))
        if ":" not in line:
            raise ValueError("{}: bad frontmatter line {!r}".format(path.name, line))
        key, val = line.split(":", 1)
        key = key.strip()
        if key in fm:
            raise ValueError("{}: duplicate key {}".format(path.name, key))
        try:
            fm[key] = _value(val.strip())
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
# _CASE_FRAME overrides the family lines or the core text of a case; every other case is an aiqt rule
# whose core is an H1 and one paragraph.
_DETAIL_SRC = """---
corpus-id: selfd1
origin: pack
{family}slug: gen-rules-selftest-detail
{keys}---
{core}{body}"""
_AIQT_FAMILY = "family: aiqt\ntier: 10\nfacet: QUALI\n"
_CORE = "# Gen-rules detail self-test rule\n\nCore text.\n"
_TRIGGER = "detail-trigger: writing a self-test fixture\n"
_REASON = "detail-reason: self-test only, no must-fire clause moves\n"
_DETAIL_BODY = "\n## Detail\n\nDetail text.\n"
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
_FM = ("        if not line:\n            continue\n", '        if not line or line.startswith("#"):\n            continue\n')
_FENCE = (' or line.startswith("```")', "")
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
    ("non-string-value", "if not isinstance(fm[key], str) or not fm[key].strip():", "if False:",
     "non-string-trigger", 0),
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
    _revert("body-r6-frontmatter-heading", "r6-frontmatter-heading", _FM),
    _revert("body-r6-frontmatter-indented-comment", "r6-frontmatter-indented-comment", _FM),
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


def _detail_case_root(base, name):
    """A synthetic corpus holding the one rule of the named detail case. Returns the case root."""
    _case, keys, body, _expected = next(c for c in _DETAIL_CASES if c[0] == name)
    frame = dict(dict(family=_AIQT_FAMILY, core=_CORE), **_CASE_FRAME.get(name, {}))
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: an invalid-UTF-8 generated target fails closed (exit 2), not a raw "
          "UnicodeDecodeError traceback (guards the widened reconcile arm); {} detail layout case(s) "
          "hold and {} guard revert(s) each go red.".format(len(_DETAIL_CASES), len(_DETAIL_REVERTS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
