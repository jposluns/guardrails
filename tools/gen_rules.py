#!/usr/bin/env python3
"""Generate the .claude/rules/ read tree from .aiqt/core/rules/ sources (the source-and-adapter machinery).

Each source is a Markdown rule with a minimal YAML `---` frontmatter carrying its classification; its read
path is DERIVED from that frontmatter per the two-axis taxonomy (aiqt/ numbered by priority, security/
coded CIA+P). The updater's write root is `.aiqt/core/`; CI runs this in --check so the read tree can never
silently drift (including orphaned generated files with no source). Vendored `external/` trees are untouched.

RULE SOURCE FORMAT (the two-layer split; this step parses and validates it, and moves no text yet):
  A rule source is read from its raw bytes as UTF-8 with no newline translation, and a source holding
  any CR byte is refused (check_clauses reads it the same way, through read_rule_source and
  decode_rule_source, so both tools number lines alike). A source may hold at most one body line that
  reads exactly `## Detail`, at column 0, outside any fenced code block and outside any HTML comment. The
  text above it is the CORE layer and the text below it is the DETAIL layer. Two optional frontmatter
  keys go with it, on aiqt non-apex and security rules only (the apex never splits):
    detail-trigger: <a short phrase naming the operation>   required when the heading exists
    detail-reason:  <one line recording the must-fire review> optional when the heading exists
  Each of these is refused as a malformed source (exit 2, naming the file and, for a line, its number):
    - either key without the heading, a missing detail-trigger with it, or a key value that is not a
      non-empty string;
    - a second `## Detail` line;
    - a `## Detail` line, or any other line that names Detail as a heading, inside a fenced code block
      (backtick or tilde, three or more, indented up to 3 spaces) or inside an HTML comment (from `<!--`
      to the next `-->`, across lines): such a line is never the split and never skipped;
    - a fenced code block or an HTML comment that is still open at the end of the source; an HTML
      comment opens only where `<!--` starts a line (indent up to 3 spaces), so a `<!--` later in a
      line (such as inside a code span) is text, and `<!-->` is a whole comment;
    - any raw HTML block line outside a fenced code block and an HTML comment: a line whose first
      character, at indent up to 3 spaces, is `<` followed by a letter, `/`, `?`, or a `!` that does not
      open an HTML comment (such as `<pre>`, `<div>`, `<details>`, `</div>`, `<h2>`), so a `## Detail`
      inside an HTML block can never be taken as the split;
    - an ATX heading line that names Detail in another form (another level, case, plural, spacing,
      emphasis or backticks, or trailing text), at any indent, and also behind blockquote (`>`) or list
      item (`-`, `*`, `+`, `1.`, `1)`) markers;
    - a setext heading naming Detail: a line reading `Detail` or `Details` (any case, any indent, with
      or without emphasis, backticks or trailing text, and also behind blockquote or list item markers)
      followed, directly or after more non-blank text lines, by a line of `=` or `-` characters;
    - an empty core layer or an empty detail layer, where a layer is empty when it holds nothing but
      blank lines and HTML comments.
  The generated read tree is still the whole source, byte for byte; the separate detail output and its
  pointer sentence come in a later step.
  gen_rules.py           regenerate .claude/rules/{aiqt,security}/
  gen_rules.py --check   fail (exit 1) on drift; exit 2 on a malformed source or a read/write failure
  gen_rules.py --self-test  assert an invalid-UTF-8 generated target fails closed (exit 2), that each
                            listed detail layout case gets its expected exit, and that each guard in
                            the revert table, put back in a scratch copy, changes its case's exit
"""
import os
import re
import sys
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
# heading line that names Detail (the forms listed there) is refused rather than read as core, so a near
# miss can never leave the author believing text was split when it was not.
DETAIL_HEADING = "## Detail"
DETAIL_KEYS = {"detail-trigger", "detail-reason"}
_NEAR_DETAIL_RE = re.compile(r'^\s{0,3}#{1,6}\s*details?\b', re.IGNORECASE)
_CONTAINER_RE = re.compile(r'^[ \t]*(?:>|[-*+](?=[ \t])|\d{1,9}[.)](?=[ \t]))[ \t]*')
_SETEXT_TEXT_RE = re.compile(r'^[ \t]*details?\b', re.IGNORECASE)
# Emphasis markers and backticks, dropped from a heading's text before it is compared with Detail.
_HEADING_MARKUP_RE = re.compile(r'[*_`]')
# A raw HTML block start (the narrow body grammar admits none): `<` at indent 0-3 followed by a letter,
# `/`, `?`, or `!` that does not open an HTML comment. An HTML comment opens only at the start of a line.
_HTML_BLOCK_RE = re.compile(r'^ {0,3}<(?:[A-Za-z]|/|\?|!(?!--))')
_COMMENT_START_RE = re.compile(r'^ {0,3}<!--')
_SETEXT_UNDERLINE_RE = re.compile(r'^ {0,3}(?:=+|-+)[ \t]*$')
_FENCE_OPEN_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
_FENCE_CLOSE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})[ \t]*$')

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
        if not line or line.startswith("#"):
            continue
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


def _scan_body(text, name):
    """Each body line as (number, line, hidden, visible): hidden is "a fenced code block" or "an HTML
    comment" when the line starts inside one, else None; visible is the line with HTML comment text
    removed (a fenced line is visible as it stands). Raises ValueError on a fence or a comment still open
    at the end of the source."""
    first_body = body_first_line(text, name)
    out, fence, comment = [], None, None  # fence: (char, length, opening line); comment: opening line
    for number, line in enumerate(text.split("\n")[first_body - 1:], first_body):
        if fence is not None:
            out.append((number, line, "a fenced code block", line))
            close = _FENCE_CLOSE_RE.match(line)
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= fence[1]:
                fence = None
            continue
        hidden = None if comment is None else "an HTML comment"
        if comment is None:
            opening = _FENCE_OPEN_RE.match(line)
            if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
                fence = (opening.group(1)[0], len(opening.group(1)), number)
                out.append((number, line, None, line))
                continue
        if comment is None and _HTML_BLOCK_RE.match(line):
            raise ValueError("{}: line {}: raw HTML block line {!r}; a rule body holds no raw HTML (a "
                             "heading inside it is never the split)".format(name, number, line))
        if comment is None and not _COMMENT_START_RE.match(line):
            out.append((number, line, hidden, line))
            continue
        visible, pos = [], 0
        while True:
            if comment is not None:
                end = line.find("-->", pos)
                if end == -1:
                    break
                comment, pos = None, end + 3
            else:
                start = line.find("<!--", pos)
                if start == -1:
                    visible.append(line[pos:])
                    break
                visible.append(line[pos:start])
                comment, pos = number, start + 2
        out.append((number, line, hidden, "".join(visible)))
    if fence is not None:
        raise ValueError("{}: line {}: fenced code block is never closed".format(name, fence[2]))
    if comment is not None:
        raise ValueError("{}: line {}: HTML comment is never closed".format(name, comment))
    return out


def _uncontained(line):
    """The line with any leading blockquote and list item markers removed."""
    while True:
        container = _CONTAINER_RE.match(line)
        if not container:
            return line
        line = line[container.end():]


def _names_detail(line):
    """True when the line, read as an ATX heading at any indent and behind any blockquote or list item
    markers, names Detail (any level, case, plural, spacing, emphasis, backticks, or trailing text)."""
    line = _HEADING_MARKUP_RE.sub("", _uncontained(line))
    return bool(_NEAR_DETAIL_RE.match(line.lstrip(" \t")))


def _setext_names_detail(scanned, index):
    """True when the scanned line at index, behind any blockquote or list item markers and without
    emphasis or backticks, starts with Detail and its paragraph (the following non-blank lines outside a
    fence or comment) is closed by a setext underline of `=` or `-` characters."""
    if not _SETEXT_TEXT_RE.match(_HEADING_MARKUP_RE.sub("", _uncontained(scanned[index][1]))):
        return False
    for _number, line, hidden, _visible in scanned[index + 1:]:
        if hidden is not None or not _uncontained(line).strip():
            return False
        if _SETEXT_UNDERLINE_RE.match(_uncontained(line)):
            return True
    return False


def detail_heading_line(text, name):
    """The 1-based line number of the single `## Detail` heading in a rule source's body, or None when the
    source has none. Raises ValueError on each refused layout that RULE SOURCE FORMAT lists, other than
    the frontmatter keys and empty layers (check_detail). check_clauses derives each clause's layer from
    it."""
    scanned = _scan_body(text, name)
    found = None
    for index, (number, line, hidden, _visible) in enumerate(scanned):
        if hidden is not None:
            if line == DETAIL_HEADING or _names_detail(line):
                raise ValueError("{}: line {}: heading {!r} names Detail inside {}; it is never the split "
                                 "and never skipped".format(name, number, line, hidden))
            continue
        if line == DETAIL_HEADING:
            if found is not None:
                raise ValueError("{}: more than one '{}' heading (lines {} and {})".format(
                    name, DETAIL_HEADING, found, number))
            found = number
        elif _names_detail(line):
            raise ValueError("{}: line {}: heading {!r} names Detail but is not exactly '{}'".format(
                name, number, line, DETAIL_HEADING))
        elif _setext_names_detail(scanned, index):
            raise ValueError("{}: line {}: setext heading {!r} names Detail but is not exactly '{}'".format(
                name, number, line, DETAIL_HEADING))
    return found


def check_detail(text, fm, name):
    """Validate the two-layer split of one source: the detail keys go with exactly one `## Detail` heading,
    detail-trigger is required with it, each key is a non-empty string, and neither layer is empty (blank
    lines and HTML comments only). Returns the heading's line number, or None. Raises ValueError (a
    malformed source) on any violation."""
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
    scanned = _scan_body(text, name)
    if not "".join(visible for number, _l, _h, visible in scanned if number < heading).strip():
        raise ValueError("{}: the core layer above '{}' is empty".format(name, DETAIL_HEADING))
    if not "".join(visible for number, _l, _h, visible in scanned if number > heading).strip():
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
_DETAIL_CASES = (
    ("detail-with-trigger", _TRIGGER + _REASON, _DETAIL_BODY, 0),
    ("security-detail", _TRIGGER, _DETAIL_BODY, 0),
    ("fenced-code-detail", _TRIGGER, "\n## Detail\n\n```\ncode\n```\n", 0),
    ("closed-fence-and-comment", "", "\n```text\ncode\n```\n\n~~~~\ncode\n~~~~\n\n<!--\nnote\n-->\n", 0),
    ("detail-without-trigger", _REASON, _DETAIL_BODY, 2),
    ("trigger-without-detail", _TRIGGER, "", 2),
    ("reason-without-detail", _REASON, "", 2),
    ("two-detail-headings", _TRIGGER, _DETAIL_BODY + "\n## Detail\n\nMore detail.\n", 2),
    ("near-miss-heading", "", "\n### Detail\n\nDetail text.\n", 2),
    ("near-miss-lowercase", "", "\n## detail\n\nDetail text.\n", 2),
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
    ("tilde-fenced-heading", _TRIGGER, "\n   ~~~~\n## Detail\n~~~~~\n\nDetail text.\n", 2),
    ("fenced-near-miss", "", "\n````md\n## Details\n````\n", 2),
    ("comment-heading", _TRIGGER, "\n<!--\n## Detail\n-->\n\nDetail text.\n", 2),
    ("comment-near-miss", "", "\n<!-- a note\n### Detail\n-->\n", 2),
    ("unterminated-fence", "", "\n```\ncode\n", 2),
    ("unterminated-comment", "", "\n<!-- note\n", 2),
    ("cr-byte", "", "\nCore line two.\r\n", 2),
    ("empty-core", _TRIGGER, _DETAIL_BODY, 2),
    ("empty-detail", _TRIGGER, "\n## Detail\n", 2),
    ("comment-only-detail", _TRIGGER, "\n## Detail\n\n<!-- nothing -->\n\n", 2),
    ("non-string-trigger", "detail-trigger: [writing]\n", _DETAIL_BODY, 2),
    ("longer-fence-close", _TRIGGER, "\n````md\n```\n## Detail\n```\n````\n", 2),
    ("mixed-fence-close", _TRIGGER, "\n```\n~~~\n## Detail\n~~~\n```\n", 2),
    ("backtick-info-not-fence", _TRIGGER, "\n```not` a fence\n" + _DETAIL_BODY, 0),
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
    ("code-span-comment-opener", _TRIGGER, "\nUse `<!--` to open a comment.\n" + _DETAIL_BODY, 0),
    ("empty-comment", _TRIGGER, "\n<!-->\n" + _DETAIL_BODY, 0),
)
_CASE_FRAME = {
    "security-detail": {"family": "family: security\nfacet: SECI\n"},
    "empty-core": {"core": "<!-- no visible core -->\n"},
}
# Red on revert: each guard put back to its pre-fix form in a scratch copy of this module, loaded through
# importlib, must then turn its case to the reverted exit (0 for a guard that refuses, 2 for the security
# keyset that admits the detail keys). (name, fixed text, reverted text, case, exit with the guard reverted)
_NEAR_RE_FIXED = "_NEAR_DETAIL_RE = re.compile(r'^\\s{0,3}#{1,6}\\s*details?\\b', re.IGNORECASE)"
_NEAR_RE_NARROW = "_NEAR_DETAIL_RE = re.compile(r'^#{3}\\s*Detail\\b')"
_HTML_RE_FIXED = "_HTML_BLOCK_RE = re.compile(r'^ {0,3}<(?:[A-Za-z]|/|\\?|!(?!--))')"
_HTML_GUARD = "if comment is None and _HTML_BLOCK_RE.match(line):"
_MARKUP_FIXED = 'line = _HEADING_MARKUP_RE.sub("", _uncontained(line))'
_SETEXT_TEXT_FIXED = '_SETEXT_TEXT_RE.match(_HEADING_MARKUP_RE.sub("", _uncontained(scanned[index][1])))'
_FENCE_CLOSE_FIXED = "close.group(1)[0] == fence[0] and len(close.group(1)) >= fence[1]"
_DETAIL_REVERTS = (
    ("missing-trigger", 'if "detail-trigger" not in fm:', "if False:", "detail-without-trigger", 0),
    ("orphan-keys", "        if present:\n", "        if False:\n", "trigger-without-detail", 0),
    ("second-heading", "if found is not None:", "if False:", "two-detail-headings", 0),
    ("near-miss", "elif _names_detail(line):", "elif False:", "near-miss-heading", 0),
    ("near-miss-lowercase", _NEAR_RE_FIXED, _NEAR_RE_NARROW, "near-miss-lowercase", 0),
    ("near-miss-two-spaces", _NEAR_RE_FIXED, _NEAR_RE_NARROW, "near-miss-two-spaces", 0),
    ("near-miss-colon", _NEAR_RE_FIXED, _NEAR_RE_NARROW, "near-miss-colon", 0),
    ("near-miss-plural", _NEAR_RE_FIXED, _NEAR_RE_NARROW, "near-miss-plural", 0),
    ("any-indent", 'return bool(_NEAR_DETAIL_RE.match(line.lstrip(" \\t")))',
     "return bool(_NEAR_DETAIL_RE.match(line))", "indented-heading", 0),
    ("blockquote-marker", "container = _CONTAINER_RE.match(line)", "container = None", "blockquote-heading", 0),
    ("list-marker", "container = _CONTAINER_RE.match(line)", "container = None", "list-item-heading", 0),
    ("ordered-list-marker", "container = _CONTAINER_RE.match(line)", "container = None",
     "ordered-list-heading", 0),
    ("setext", "elif _setext_names_detail(scanned, index):", "elif False:", "setext-heading", 0),
    ("fence-hidden", 'out.append((number, line, "a fenced code block", line))',
     "out.append((number, line, None, line))", "backtick-fenced-heading", 0),
    ("tilde-fence", "_FENCE_OPEN_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')",
     "_FENCE_OPEN_RE = re.compile(r'^ {0,3}(`{3,})(.*)$')", "tilde-fenced-heading", 0),
    ("comment-hidden", 'hidden = None if comment is None else "an HTML comment"', "hidden = None",
     "comment-heading", 0),
    ("fence-unterminated", "    if fence is not None:\n        raise", "    if False:\n        raise",
     "unterminated-fence", 0),
    ("comment-unterminated", "    if comment is not None:\n        raise", "    if False:\n        raise",
     "unterminated-comment", 0),
    ("cr-byte", 'if b"\\r" in raw:', "if False:", "cr-byte", 0),
    ("empty-core", 'if not "".join(visible for number, _l, _h, visible in scanned if number < heading).strip():',
     "if False:", "empty-core", 0),
    ("empty-layer", 'if not "".join(visible for number, _l, _h, visible in scanned if number > heading).strip():',
     "if False:", "empty-detail", 0),
    ("comment-only-layer",
     'if not "".join(visible for number, _l, _h, visible in scanned if number > heading).strip():',
     'if not "".join(_l for number, _l, _h, visible in scanned if number > heading).strip():',
     "comment-only-detail", 0),
    ("non-string-value", "if not isinstance(fm[key], str) or not fm[key].strip():", "if False:",
     "non-string-trigger", 0),
    ("corpus-wiring", "check_detail(read_rule_source(src), fm, src.name)", "pass", "detail-without-trigger", 0),
    ("security-detail-keys", '_check_keys(fm, BASE_KEYS | {"facet", "secondary"} | MAP_KEYS | DETAIL_KEYS, name)',
     '_check_keys(fm, BASE_KEYS | {"facet", "secondary"} | MAP_KEYS, name)', "security-detail", 2),
    ("fence-close-length", _FENCE_CLOSE_FIXED, "close.group(1)[0] == fence[0]", "longer-fence-close", 0),
    ("fence-close-char", _FENCE_CLOSE_FIXED, "len(close.group(1)) >= fence[1]", "mixed-fence-close", 0),
    ("backtick-info", 'if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):',
     "if opening:", "backtick-info-not-fence", 2),
    ("html-pre", _HTML_GUARD, "if False:", "html-pre", 0),
    ("html-div", _HTML_GUARD, "if False:", "html-div", 0),
    ("html-script", _HTML_GUARD, "if False:", "html-script", 0),
    ("html-details", _HTML_GUARD, "if False:", "html-details", 0),
    ("html-heading", _HTML_GUARD, "if False:", "html-heading-near-miss", 0),
    ("html-slash", _HTML_RE_FIXED, _HTML_RE_FIXED.replace("|/", ""), "html-close-tag", 0),
    ("html-question", _HTML_RE_FIXED, _HTML_RE_FIXED.replace("|\\?", ""), "html-processing", 0),
    ("html-bang", _HTML_RE_FIXED, _HTML_RE_FIXED.replace("|!(?!--)", ""), "html-declaration", 0),
    ("setext-text-container", _SETEXT_TEXT_FIXED,
     '_SETEXT_TEXT_RE.match(_HEADING_MARKUP_RE.sub("", scanned[index][1]))', "list-setext", 0),
    ("setext-underline-container", "if _SETEXT_UNDERLINE_RE.match(_uncontained(line)):",
     "if _SETEXT_UNDERLINE_RE.match(line):", "blockquote-setext", 0),
    ("setext-paragraph", "        if _SETEXT_UNDERLINE_RE.match(_uncontained(line)):\n            return True\n",
     "        return bool(_SETEXT_UNDERLINE_RE.match(_uncontained(line)))\n", "multiline-setext", 0),
    ("setext-markup", _SETEXT_TEXT_FIXED, "_SETEXT_TEXT_RE.match(_uncontained(scanned[index][1]))",
     "bold-setext", 0),
    ("heading-bold", _MARKUP_FIXED, "line = _uncontained(line)", "bold-near-miss", 0),
    ("heading-underscore", _MARKUP_FIXED, "line = _uncontained(line)", "underscore-near-miss", 0),
    ("heading-backtick", _MARKUP_FIXED, "line = _uncontained(line)", "backtick-near-miss", 0),
    ("comment-line-start", "if comment is None and not _COMMENT_START_RE.match(line):", "if False:",
     "code-span-comment-opener", 2),
    ("comment-empty", "comment, pos = number, start + 2", "comment, pos = number, start + 4", "empty-comment", 2),
)


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
    (never exec). The mutation is confined to the text above the self-test section, so the revert table
    itself is never the match. sys.path is restored after the load."""
    import importlib.util
    source = Path(__file__).read_text(encoding="utf-8")
    production, sep, tests = source.partition("\n# --- self-test ")
    if not sep or production.count(old) != 1:
        raise AssertionError("revert {}: the fixed text must occur exactly once in the production code"
                             .format(label))
    path = base / "gen_rules_reverted_{}.py".format(label.replace("-", "_"))
    path.write_text(production.replace(old, new, 1) + sep + tests, encoding="utf-8")
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
