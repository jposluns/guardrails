#!/usr/bin/env python3
"""Generate the .claude/rules/ read tree from .aiqt/core/rules/ sources (the source-and-adapter machinery).

Each source is a Markdown rule with a minimal YAML `---` frontmatter carrying its classification; its read
path is DERIVED from that frontmatter per the two-axis taxonomy (aiqt/ numbered by priority, security/
coded CIA+P). The updater's write root is `.aiqt/core/`; CI runs this in --check so the read tree can never
silently drift (including orphaned generated files with no source). Vendored `external/` trees are untouched.

RULE SOURCE FORMAT (the two-layer split; this step parses and validates it and renders the full text from
it, and no live source uses it yet):
  A rule source is read from its raw bytes as UTF-8 with no newline translation, and a source holding
  any CR byte is refused (check_clauses reads it the same way, through read_rule_source and
  decode_rule_source, so both tools number lines alike). The split is the one body line that reads
  exactly `## Detail` (column 0, no trailing whitespace) with a blank line directly above it. The text
  above it is the CORE layer and the text below it is the DETAIL layer. Two optional frontmatter keys go
  with it, on aiqt non-apex and security rules only (the apex never splits):
    detail-trigger: <a short phrase naming the operation>   required when the split exists
    detail-reason:  <one line recording the must-fire review> optional when the split exists
  BODY GRAMMAR: this reader models no Markdown. Every body line
  must be one of these shapes, chosen so that a CommonMark renderer can build no block from the body
  other than ATX headings and paragraphs, so its rendered headings are exactly its title line and its
  `## Detail` line by construction:
    - a blank line (empty);
    - the title line, which is the first non-blank body line and the only one: `# ` and non-blank text
      that is printable ASCII (0x20 to 0x7E) holding none of [ ] < > & ~ * _, a backslash or a
      backtick (`(` and `)` are allowed: with no `[` no link or image can form);
    - the split line, exactly `## Detail`, at most once;
    - a prose line: printable ASCII (0x20 to 0x7E) holding no `<`, `[`, `]` or backslash; its first
      character is none of a space, a tab, # > - + * = ~ | < [ ! _ and `:`, and it does not start
      with three backticks (a fence) or like an ordered list item (digits, then `.` or `)`, then a
      space or the end of the line). A single leading backtick is allowed (a code span; the live
      corpus starts two lines with one), and so are `>`, `|`, `_` and `&` after the first character.
  FRONTMATTER: this reader and a YAML reader must see the
  same keys and the same values, so no YAML is modelled: every known key has one declared value type,
  a value is accepted only in a shape of its type, and anything else is refused. The frontmatter is
  split into lines at LF only, as the body is. Each raw line, before any strip, holds no character of
  Unicode category Cc, Cf, Cn, Co, Cs, Zl or Zp (so no tab, U+0085, U+2028, U+2029, U+001C to U+001F,
  U+FFFE or U+FFFF), except ZWNJ and ZWJ (U+200C and U+200D). A line holding only spaces is skipped. Every
  other line is `key: value`: the key at column 0, optional spaces, `:`, at least one space, the value,
  and optional trailing spaces. A comment line (spaces, then `#`; YAML ignores it) is refused, and so is any other
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
  refused. Any frontmatter outside the FRONTMATTER grammar above is refused, and the refusal names the
  file and quotes the line or value. The commonest examples (the list is not complete) are:
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
      including in a comment line: delete it (ZWNJ and ZWJ, U+200C and U+200D, are allowed in
      free-text values and comment lines);
    - a non-ASCII space (U+00A0, U+3000 or any other Unicode space separator) anywhere in a line or
      alone on a line, except after a comment's `#` and inside a detail-trigger or detail-reason
      value (not as its first or last character): use an ASCII space or delete it;
    - non-ASCII digits in a number (`tier: 10` written in Arabic-Indic digits U+0661 U+0660 or in
      fullwidth digits U+FF11 U+FF10): write ASCII digits;
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
  FULL RENDER (full_body): each generated file is its source's full render. A source with no split renders
  as itself, byte for byte. In a split source the detail layer holds the rule's current text verbatim, and
  the source renders as its unsplit original, byte for byte: the frontmatter with every detail-trigger and
  detail-reason line dropped, then the body through its title line, then every line below the `## Detail`
  line (the core layer below the title and the split line are dropped). So the split is invisible in the
  read tree, in write mode and in the --check drift compare. core_layer returns a split source's core text
  below its title (blank lines at either end dropped), and None for a source with no split.
REFERENCE TREE (.aiqt/reference/rules/{aiqt,security}/, REFERENCE_DIR): the full-rule copies that the CORE
  render's pointers name (tools/gen_rules_core.py REFERENCE_PREFIX). Each file is the same full render
  (full_body) of its source at the same derived path as in .claude/rules/. The reference tree is compared
  byte for byte (a CRLF or a lone CR in place of an LF is drift, and so is a target that is not UTF-8) and
  written as bytes. Every read, write and deletion is confined to the two declared output trees
  (REFERENCE_DIR/aiqt/ and REFERENCE_DIR/security/) and bound beneath directory descriptors with the
  no-follow walk tools/_adapter_compose.py uses for its targets (each directory opened beneath its parent's
  descriptor with O_DIRECTORY|O_NOFOLLOW; a write goes to a new temporary file renamed over the target).
  Before anything is read, written or deleted in either tree, the whole of REFERENCE_DIR is classified
  without following a link, and each of these is exit 2 in both modes, named, with nothing read, written or
  deleted through it: a symbolic link (wherever it points, inside the root too) or another non-directory at
  .aiqt/, .aiqt/reference/, REFERENCE_DIR or a family directory; a symbolic link, FIFO, socket or device
  anywhere in a family directory; a reference target that is not a regular file; and any entry of
  REFERENCE_DIR other than the two family directories, or of .aiqt/reference/ other than REFERENCE_DIR
  (outside the declared trees, so it is never deleted: remove it by hand). Every file in a family directory
  that no source renders is an orphan (drift), whatever its name, and so is every directory there that holds
  no rendered file. Permission bits are not compared: a reference file whose mode alone differs (an added
  execute bit, say) is not drift, and a rewrite keeps the existing file's mode. That nothing loads the
  reference tree is inferred, not observed on a client: it is outside .claude/ and the paths the
  instruction-budget gate models as loaded.
READ TREE (.claude/rules/{aiqt,security}/, RULES_DIR): read, written and pruned through the same walk, confined
  to its two declared output trees, and classified the same way before anything in either tree is read,
  written or deleted: a symbolic link (wherever it points, inside the root too, such as a family directory
  linked to .aiqt/core/rules/) or another non-directory at .claude/, RULES_DIR or a family directory, and a
  symbolic link, FIFO, socket or device anywhere in a family directory, is exit 2 in both modes, named, with
  nothing read, written or deleted through it; so is a read-tree target that is not a regular file. Its
  compare is unchanged: each target is read as UTF-8 text with every CRLF and lone CR read as LF (a target
  that is not UTF-8 is exit 2), only a *.md file no source renders is an orphan, and RULES_DIR's other entries
  (its README.md) are neither judged nor touched. A target is rewritten as a new file renamed over it, never
  written through a link or a second hard link.
  gen_rules.py           regenerate .claude/rules/{aiqt,security}/ and .aiqt/reference/rules/{aiqt,security}/
  gen_rules.py --check   fail (exit 1) on drift; exit 2 on a malformed source or manifest, a rule mapping
                         to an id its manifest marks withdrawn, or a read/write failure
  gen_rules.py --self-test  assert an invalid-UTF-8 generated target, a source path that is not a
                            directory or a symlink to a directory inside the root, and a .claude/, a
                            .claude/rules or a family directory symlinked outside or inside the root fail
                            closed (exit 2), and the MAP_KEYS read refusal exits 2 with empty stdout under each
                            stderr condition; that each listed detail layout case gets its expected
                            exit, and that each guard in the revert table, put back in a scratch copy,
                            changes its case's exit; and that each listed split source renders byte-equal
                            to its unsplit original in write and check mode, with core_layer giving its
                            core, and that each split-render guard, put back, makes a split case fail;
                            and that the reference tree is generated as each source's full render, a
                            missing, edited, extra or CRLF-altered reference file, an empty or orphaned
                            subdirectory is drift (exit 1) with --check writing nothing, a malformed
                            source, a reference target that is a symbolic link, a reference directory
                            linked outside the root, a family, REFERENCE_DIR or a subdirectory linked
                            inside the root, an orphan symbolic link, a stray entry beside the families
                            or beside REFERENCE_DIR and a family swapped for a link after the scan are
                            exit 2 with nothing written or deleted, as is a platform that cannot list a
                            directory descriptor; that in the read tree a family linked to the sources,
                            .claude/rules or a subdirectory linked inside the root, a target or an orphan
                            that is a symbolic link, a FIFO and a family swapped for a link after the scan
                            are exit 2 with nothing written or deleted; and each generated-tree guard, put
                            back, makes its named case fail by its verdict, not by an exception
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_rules.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import html
import os
import re
import stat
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, unknown_args_refused  # noqa: E402
from _standards import (dir_chain_or_absent, dir_or_absent, dir_present, load_manifests, map_keys,  # noqa: E402
                        withdrawn_mappings)
# The reference tree is read, written and pruned only through the no-follow, descriptor-bound walk the adapter
# targets already use (each directory opened beneath its parent's descriptor with O_DIRECTORY|O_NOFOLLOW, the
# final name judged absent, a regular file or refused), never a second implementation of it.
from _adapter_compose import (ComposeError, _open_dir_component, _read_target, _require_dir_fd,  # noqa: E402
                              _write_target)

TIER_FACETS = {"10": {"ACCUR", "INTEG", "QUALI", "TRUST"}, "20": {"PROGR"},
               "30": {"SPEED"}, "40": {"COST"}}
CIA_FACETS = {"SECC", "SECI", "SECA", "SECP"}  # SECC=Confidentiality SECI=Integrity SECA=Availability SECP=Privacy
# The global known-facet set: any rule may carry ANY of these as a `secondary` tag, cross-family (an aiqt
# rule may tag a security facet and vice versa). Security codes are namespaced (SEC*) so none collides with
# an AIQT facet (e.g. SECI vs the AIQT INTEG). The secondary element rules (known-facet, differs-from-
# primary, no-repeat) come from the maintainer's recorded decision (design-of-record: secondary = any known
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
    # The canonical floor-guard shape (tools/check_python_floor.py GUARD_TEMPLATE): the write and its
    # flush are best-effort, and os._exit skips the interpreter-exit flush of the std streams, so the
    # exit stays 2 when sys.stderr is None, fails on write or fails on flush, and stdout stays empty
    # (`raise SystemExit` became exit 120 under a failing flush, and print, with sys.stderr None, wrote
    # the diagnostic to stdout instead).
    try:
        sys.stderr.write("error: cannot read {}/.aiqt/standards/ (fail-closed): {}\n".format(
            repo_root(), _exc))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)
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
_CONTAINER_RE = re.compile(r'^[ \t]*(?:>|[-*+](?=[ \t]|$)|\d{1,9}[.)](?=[ \t]|$))[ \t]*', re.ASCII)
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
    {"target": ".aiqt/reference/rules/aiqt/", "kind": "tree",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_rules.py"},
    {"target": ".aiqt/reference/rules/security/", "kind": "tree",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_rules.py"},
)
# The reference tree the CORE render's pointers name (see REFERENCE TREE above; tools/gen_rules_core.py
# REFERENCE_PREFIX is this directory with a trailing slash).
REFERENCE_DIR = ".aiqt/reference/rules"
# The two declared reference output trees (the gensrc rows above): REFERENCE_DIR/<family>/.
REFERENCE_FAMILIES = ("aiqt", "security")
# The generated read tree and its two declared output trees (the first two gensrc rows above): RULES_DIR/<family>/,
# read, written and pruned through the same descriptor-bound walk as the reference tree.
RULES_DIR = ".claude/rules"
RULES_FAMILIES = ("aiqt", "security")
# The directory opener of the generated-tree scan: _adapter_compose's no-follow component open (a symlink or a
# non-directory is a ComposeError naming it; an absent component raises FileNotFoundError).
_ref_open = _open_dir_component


def _reference_dir_fd(root, parts):
    """A descriptor for the directory root/<parts>, which the caller closes, or None when a component is absent.
    root is opened as named (the trusted anchor); every component below it is opened beneath the previous one's
    descriptor by _ref_open, so a symlink (wherever it points, inside the root or not) or a non-directory at
    ANY component (.aiqt/ included) is a ComposeError naming it, never followed."""
    fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for i, part in enumerate(parts):
            try:
                child = _ref_open(fd, part, "/".join(parts), "/".join(parts[:i + 1]))
            except FileNotFoundError:
                return None
            fd, parent = child, fd
            os.close(parent)
        held, fd = fd, None
        return held
    finally:
        if fd is not None:
            os.close(fd)


def _tree_entry_refusal(tree, rel, st):
    """The refusal of an entry under the generated tree `tree` that is neither a directory nor a regular file."""
    return ComposeError("{}/{} is {}; {} is never read, written or pruned through it, and neither mode touches it "
                        "(remove it by hand)".format(
                            tree, rel, "a symlink" if stat.S_ISLNK(st.st_mode)
                            else "not a regular file or a directory", tree))


def _tree_scan(root, tree, families, desired, every_name):
    """Classify every entry of the family directories of the generated tree `tree` (REFERENCE_DIR or RULES_DIR)
    beneath directory descriptors, following no link, before anything is read, written or deleted: return
    (orphans, strays). With every_name (REFERENCE_DIR), orphans lists every regular file in a family directory that
    no source renders and every directory there that holds no desired path, and strays lists every entry of `tree`
    other than the family directories, whatever its kind (outside the declared output trees, so never deleted, by
    their repo-relative path). Without it (RULES_DIR, whose contract predates the walk), orphans lists only the
    regular files named *.md that no source renders, a directory is never an orphan, and the other entries of `tree`
    (its README.md) are not listed. Each orphan is relative to `tree` and listed after its contents (so pruning in
    order empties a directory before removing it). A symlink or other non-directory at any component of `tree` or
    at a family directory, and a symlink, FIFO, socket or device anywhere in a family directory, raises
    ComposeError naming it. An absent tree or family is no entries. A platform without descriptor-relative
    resolution (the dir_fd forms _adapter_compose requires, os.scandir on a descriptor, and os.unlink and os.rmdir
    with dir_fd) raises ComposeError first (exit 2 in both modes): there is no path-name fallback."""
    _require_dir_fd()
    if (os.scandir not in os.supports_fd or os.unlink not in os.supports_dir_fd
            or os.rmdir not in os.supports_dir_fd):
        raise ComposeError("this platform cannot list a directory descriptor or remove a name beneath one "
                           "(os.scandir on a descriptor, os.unlink and os.rmdir with dir_fd); a generated tree "
                           "is never read, written or pruned through a path name, so nothing was read or written")
    keep = {rel.rsplit("/", 1)[0] for rel in desired if "/" in rel}
    for rel in list(keep):
        while "/" in rel:
            rel = rel.rsplit("/", 1)[0]
            keep.add(rel)
    orphans, strays = [], []

    def walk(dir_fd, rel):
        with os.scandir(dir_fd) as listed:
            names = sorted(entry.name for entry in listed)
        for name in names:
            sub = rel + "/" + name
            st = os.lstat(name, dir_fd=dir_fd)
            if stat.S_ISDIR(st.st_mode):
                child = _ref_open(dir_fd, name, tree + "/" + sub, tree + "/" + sub)
                try:
                    walk(child, sub)
                finally:
                    os.close(child)
                if every_name and sub not in keep:
                    orphans.append(sub)
            elif stat.S_ISREG(st.st_mode):
                if sub not in desired and (every_name or name.endswith(".md")):
                    orphans.append(sub)
            else:
                raise _tree_entry_refusal(tree, sub, st)

    top = _reference_dir_fd(root, tuple(tree.split("/")))
    if top is None:
        return orphans, strays
    try:
        if every_name:
            with os.scandir(top) as listed:
                strays.extend(sorted(tree + "/" + entry.name for entry in listed if entry.name not in families))
        for family in families:
            try:
                fam = _ref_open(top, family, tree + "/" + family, tree + "/" + family)
            except FileNotFoundError:
                continue
            try:
                walk(fam, family)
            finally:
                os.close(fam)
    finally:
        os.close(top)
    return orphans, strays


def _reference_scan(root, desired):
    """_tree_scan of REFERENCE_DIR (every name in a family judged), whose strays also list every entry of
    .aiqt/reference/ other than REFERENCE_DIR (outside the declared output trees, so never deleted)."""
    orphans, strays = _tree_scan(root, REFERENCE_DIR, REFERENCE_FAMILIES, desired, every_name=True)
    parent_dir, own = REFERENCE_DIR.rsplit("/", 1)
    parent = _reference_dir_fd(root, tuple(parent_dir.split("/")))
    if parent is not None:
        try:
            with os.scandir(parent) as listed:
                strays[:0] = sorted(parent_dir + "/" + entry.name for entry in listed if entry.name != own)
        finally:
            os.close(parent)
    return orphans, strays


def _rules_scan(root, desired):
    """_tree_scan of RULES_DIR: its orphans (the *.md files no source renders), every link and wrong-type entry at
    .claude/, RULES_DIR, a family directory or anywhere in one refused."""
    return _tree_scan(root, RULES_DIR, RULES_FAMILIES, desired, every_name=False)[0]


def _tree_prune(root, tree, rel):
    """Remove the orphan tree/rel by name beneath its parent's descriptor (_reference_dir_fd, so a symlink or
    non-directory at any component above it is refused, never followed): os.rmdir for a directory (only an
    empty one is removed) and os.unlink for a regular file (which removes the name, never the bytes a link
    names). An entry gone since the scan is left gone; one that became anything else is refused."""
    parts = tuple((tree + "/" + rel).split("/"))
    parent = _reference_dir_fd(root, parts[:-1])
    if parent is None:
        return
    try:
        try:
            st = os.lstat(parts[-1], dir_fd=parent)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(st.st_mode):
            os.rmdir(parts[-1], dir_fd=parent)
        elif stat.S_ISREG(st.st_mode):
            os.unlink(parts[-1], dir_fd=parent)
        else:
            raise _tree_entry_refusal(tree, rel, st)
    finally:
        os.close(parent)


def _unquote(tok):
    """A single scalar STRING element: strip matching quotes, else the bare token. Fail-closed.
    check_instruction_budget reads generated rule frontmatter through this; the typed FRONTMATTER
    grammar here uses _quoted and _value instead."""
    if tok and tok[0] in "\"'":
        if len(tok) >= 2 and tok[-1] == tok[0]:
            return tok[1:-1]
        raise ValueError("malformed quoted value {!r}".format(tok))
    return tok


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
    # str.isdecimal is the Unicode \d set: an integer written in another script's digits is refused, never
    # kept as a string that int() would read as a number.
    if v.removeprefix("-").isdecimal() and not v.isascii():
        raise ValueError("integer {} is written with non-ASCII digits (use 0-9)".format(ascii(v)))
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
        raise ValueError("{}: line {}: holds a CR byte (a carriage return); a rule source uses LF line endings "
                         "only".format(
            name, raw.count(b"\n", 0, raw.index(b"\r")) + 1))
    return raw.decode("utf-8")


def read_rule_source(path, adopter=False):
    """Read one rule source the one way both gen_rules and check_clauses read it (see decode_rule_source)."""
    return decode_rule_source(path.read_bytes(), path.name, adopter)


def parse_source(path, adopter=False):
    """The frontmatter of one rule source as {key: value}, by the FRONTMATTER grammar; adopter selects
    ADOPTER MODE (the placement gate). Raises ValueError on a malformed source."""
    return parse_source_text(read_rule_source(path, adopter), path.name, adopter)


def parse_source_text(text, name, adopter=False):
    """The frontmatter of a rule source's text, already decoded (see decode_rule_source); name labels
    every error and adopter selects ADOPTER MODE, as for parse_source."""
    if not text.startswith("---\n"):
        raise ValueError("{}: no frontmatter".format(name))
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("{}: unterminated frontmatter".format(name))
    fm = {}
    for raw in text[4:end].split("\n"):
        comment = raw.lstrip(" ").startswith("#")
        # ADOPTER MODE: a comment line may hold a tab after its `#` (YAML skips the line); every other
        # hidden character is still refused there, since YAML reads U+0085, U+2028 and U+2029 as breaks.
        scanned = raw.replace("\t", "") if adopter and comment else raw
        if any(unicodedata.category(ch) in _LINE_CATEGORIES and ch not in _JOINERS for ch in scanned):
            raise ValueError("{}: frontmatter line {!r} holds a hidden or unassigned character (Unicode "
                             "category Cc, Cf, Cn, Co, Cs, Zl or Zp; YAML can read a second key there or "
                             "refuse the file)".format(name, raw))
        if not raw.strip(" "):
            continue
        if comment:
            if adopter:
                continue
            raise ValueError("{}: frontmatter line {!r} is a YAML comment line (refused in the pack's own "
                             "rules: a renderer that does not read frontmatter can show it as a heading)"
                             .format(name, raw))
        if raw.startswith(" "):
            raise ValueError("{}: frontmatter line {!r} is indented; the frontmatter grammar requires each "
                             "key at column 0".format(name, raw))
        if ":" not in raw:
            raise ValueError("{}: bad frontmatter line {!r}".format(name, raw))
        key, val = raw.split(":", 1)
        key = key.strip(" ")
        if key in fm:
            raise ValueError("{}: duplicate key {}".format(name, key))
        if val[:1] != " ":
            raise ValueError("{}: frontmatter key {} is not followed by ': ' and a value (YAML reads the "
                             "line as one string, or an empty value as a null)".format(name, key))
        try:
            fm[key] = _value(key, val.strip(" "))
        except ValueError as exc:
            raise ValueError("{}: {}".format(name, exc))
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


class BodyGrammarError(ValueError):
    """A rule source the BODY GRAMMAR refuses. It is a ValueError, so every caller that maps a malformed
    source to a fail-closed exit still does, and it also carries the source name, the refused 1-based line
    (None when the body has no title line) and the channel the line belongs to ("title" or "body"), so a
    scanner that enumerates page-bound strings (check_overclaim) can report the refusal as a flagged
    finding naming the file, line and grammar message instead of raising."""

    def __init__(self, message, name, line, channel):
        super().__init__(message)
        self.source, self.line, self.channel = name, line, channel


def detail_heading_line(text, name):
    """The 1-based line number of the single accepted `## Detail` split in a rule source's body, or None
    when the source has none. Raises ValueError on each refused layout that RULE SOURCE FORMAT lists,
    other than the frontmatter keys and the empty detail layer (check_detail), as a BodyGrammarError (a
    ValueError). check_clauses derives each clause's layer from it."""
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
                raise BodyGrammarError("{}: more than one '{}' line (lines {} and {})".format(
                    name, DETAIL_HEADING, found, number), name, number, "body")
            found = number
            continue
        else:
            problem = _prose_problem(line)
        if _names_detail(line):
            raise BodyGrammarError("{}: line {}: {!r} reads as Detail but is not the split line '{}'".format(
                name, number, line, DETAIL_HEADING), name, number, "title" if number == title else "body")
        if problem is not None:
            raise BodyGrammarError("{}: line {}: {!r} is outside the body grammar ({})".format(
                name, number, line, problem), name, number, "title" if number == title else "body")
    if title is None:
        raise BodyGrammarError("{}: the body has no title line ('# ' and its text)".format(name),
                               name, None, "title")
    if found is None:
        return None
    if found > first and lines[found - 2] != "":
        raise BodyGrammarError("{}: line {}: the '{}' split needs a blank line directly above it".format(
            name, found, DETAIL_HEADING), name, found, "body")
    return found


def check_detail(text, fm, name):
    """Validate the two-layer split of one source: the detail keys go with exactly one `## Detail` split,
    detail-trigger is required with it, each key is a non-empty string, and the detail layer is not
    blank lines only (the core always holds the title line). Returns the split's line number, or None.
    Raises ValueError (a malformed source) on any violation."""
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


def _frontmatter_key(line):
    """The key of one frontmatter line as parse_source_text reads it (the text before its first `:`, spaces
    stripped), or None for a line with no `:`."""
    return line.split(":", 1)[0].strip(" ") if ":" in line else None


def _split_lines(text, name):
    """(lines, first, title, split) for a rule source's text whose body holds the `## Detail` split, or None
    when it has none: lines is the text split at LF, first the 1-based first body line, title the 1-based
    title line (the first non-blank body line) and split the 1-based split line, so first <= title < split.
    Raises ValueError as detail_heading_line does."""
    split = detail_heading_line(text, name)
    if split is None:
        return None
    first = body_first_line(text, name)
    lines = text.split("\n")
    title = next((number for number in range(first, split) if lines[number - 1] != ""), 0)
    if not title:
        raise ValueError("{}: the '{}' split has no title line above it".format(name, DETAIL_HEADING))
    return lines, first, title, split


def full_body(text, name):
    """The full render of one rule source's text (decoded by read_rule_source): what gen_rules writes to
    the .claude/rules/ read tree. A source with no `## Detail` split renders as itself, byte for byte. A
    split source renders as its unsplit original: its frontmatter with every detail-trigger and
    detail-reason line dropped, then its body through the title line, then every line below the split
    line. The core layer below the title (every line between the title and the split) and the split line
    are dropped, and the detail layer, which holds the rule's current text verbatim, follows the title
    exactly as it did before the split. Raises ValueError as detail_heading_line does."""
    found = _split_lines(text, name)
    if found is None:
        return text
    lines, first, title, split = found
    kept = [line for line in lines[1:first - 2] if _frontmatter_key(line) not in DETAIL_KEYS]
    return "\n".join(lines[:1] + kept + lines[first - 2:title] + lines[split:])


def core_layer(text, name):
    """The core layer of one rule source's text below its title: the body lines between the title line
    and the `## Detail` split, with blank lines at either end dropped, each ending in LF ("" when every
    such line is blank). None for a source with no split, whose full text is its whole body (full_body).
    Raises ValueError as detail_heading_line does."""
    found = _split_lines(text, name)
    if found is None:
        return None
    lines, _first, title, split = found
    core = lines[title:split - 1]
    while core and core[0] == "":
        core.pop(0)
    while core and core[-1] == "":
        core.pop()
    return "".join(line + "\n" for line in core)


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
    """Reconcile the .claude/rules/ read tree and the REFERENCE_DIR reference tree under root against the
    .aiqt/core/rules/ corpus. Exit 0 in sync, 1 on drift (check mode), 2 on a malformed source, a
    generated target that is not a regular file, a symlink or wrong-type entry in or above either generated
    tree, an entry of REFERENCE_DIR or .aiqt/reference/ outside the declared reference trees, or a read/write
    failure. Parameterized on root
    (rather than calling repo_root() inline) so the self-test can drive it against a synthetic tempdir
    tree, never the real repo."""
    src_dir = root / ".aiqt" / "core" / "rules"
    out_dir = root / ".claude" / "rules"
    desired = {}
    try:
        # dir_or_absent (not is_dir) inside the try: an unreadable .aiqt/ parent, or a source path that is
        # there but cannot be read as a directory, must fail closed as exit 2, not read as an absent corpus
        # (which would delete every generated file as an orphan below). A symlink to a directory inside the
        # root is read.
        # Then dir_present, bound beneath root, judges every component above it the same way, so a
        # .aiqt that is dangling, not a directory or a symlink resolving outside the root fails closed
        # too, never read as an absent corpus.
        _present, problem = dir_or_absent(src_dir, root)
        if problem is not None:
            print("error: {} {} (cannot evaluate)".format(src_dir, problem))
            return 2
        if dir_present(src_dir, root=root):
            corpus = load_corpus(src_dir)
            # Each source's frontmatter is written with its mapping ids, so a rule mapping to an id its
            # manifest marks withdrawn is refused here (exit 2, naming the rule, the key and the id), never
            # written.
            withdrawn = withdrawn_mappings(corpus, load_manifests(root / ".aiqt" / "standards", root=root))
            if withdrawn:
                print("error: {}".format("; ".join(withdrawn)))
                return 2
            # Each generated file is the source's full render (full_body): a source with no split as
            # itself, byte for byte, and a split source as its unsplit original, so the split is invisible
            # in the read tree, in write mode and in the --check drift compare alike.
            for src, _fm, rel in corpus:
                desired[rel] = full_body(read_rule_source(src), src.name)
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2
    # Reconcile even when src_dir is absent (desired empty) so orphaned generated files are never concealed.
    # The whole reconcile is fail-closed: an unreadable generated file or an unreadable generated dir at ANY
    # depth (the descriptor walk raises) becomes a clean exit 2 rather than a traceback or a concealed orphan.
    drift = []
    try:
        # dir_chain_or_absent (not is_dir, dir_present or dir_or_absent), before the first read, write or delete
        # under out_dir: .claude/rules and each family directory in it (the directory holding every generated
        # rule) are judged at every component under the root (.claude/ too), so one at any depth that is there
        # but cannot be read as a directory (a regular file, or a symlink that is dangling, whose target is not
        # a directory or resolves outside the root) is exit 2 with nothing written or deleted, never followed
        # out of the repository, and an unreadable parent raises (exit 2 below), never skipping the orphan
        # scan. A family absent here can only be created below, holding desired files alone.
        for path in (out_dir, out_dir / "aiqt", out_dir / "security"):
            _present, problem = dir_chain_or_absent(path, root)
            if problem is not None:
                print("error: {} {} (cannot evaluate)".format(path, problem))
                return 2
        # The read tree is confined to its two declared output trees, RULES_DIR/{aiqt,security}/, and judged in
        # full beneath directory descriptors before anything under it is read, written or deleted (_rules_scan,
        # the reference tree's walk): a symlink (wherever it points, inside the root too, such as a family
        # linked to the sources) or another non-directory at .claude/, RULES_DIR or a family directory, or a
        # symlink, FIFO, socket or device anywhere in a family, is exit 2 in both modes, named and never followed.
        rules_orphans = _rules_scan(root, desired)
        # The reference tree is confined to its two declared output trees, REFERENCE_DIR/{aiqt,security}/, and
        # is judged in full before anything under either tree is written or deleted: _reference_scan walks it
        # beneath directory descriptors, so a symlink (wherever it points, inside the root too) or another
        # non-directory at .aiqt/, .aiqt/reference/, REFERENCE_DIR or a family directory, or a symlink, FIFO,
        # socket or device anywhere in a family, is exit 2 in both modes, named and never followed. Any other
        # entry of REFERENCE_DIR or of .aiqt/reference/ is outside the declared trees: exit 2 in both modes, never
        # deleted.
        orphans, strays = _reference_scan(root, desired)
        if strays:
            print("error: {} outside the declared reference trees {}/{{{}}}/; neither mode reads, writes or deletes "
                  "it (remove it by hand) (cannot evaluate)".format(
                      ", ".join(strays), REFERENCE_DIR, ",".join(REFERENCE_FAMILIES)))
            return 2
        # Each reference target is read once, before anything is written, beneath the descriptors the walk
        # holds (_read_target): absent is None, a regular file is its bytes, and anything else there (a
        # symbolic link wherever it points, a directory or another kind) is exit 2 in both modes.
        held = {}
        for rel in sorted(desired):
            held[rel] = _read_target(root, REFERENCE_DIR + "/" + rel)
        # Each read-tree target is read the same way, before anything is written, and compared as text as before
        # the walk: decoded as UTF-8 (a target that is not UTF-8 is exit 2) with every CRLF and lone CR read as
        # LF, the newline translation Path.read_text applied, so a line-ending change alone is not drift here.
        current = {}
        for rel in sorted(desired):
            raw = _read_target(root, RULES_DIR + "/" + rel)
            current[rel] = None if raw is None else raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        # Written as the render's UTF-8 bytes (the bytes Path.write_text wrote) beneath the descriptors the walk
        # holds (_write_target: missing directories made beneath the held descriptor, a new temporary file renamed
        # over the target, never written through a link or a second hard link).
        for rel, content in sorted(desired.items()):
            if current[rel] != content:
                drift.append(rel)
                if not check:
                    _write_target(root, RULES_DIR + "/" + rel, content.encode("utf-8"))
        # Every *.md file in a read-tree family directory that no source renders is an orphan, pruned beneath its
        # parent's descriptor (other names and directories there are left alone, as before the walk).
        for orphan in rules_orphans:
            drift.append("orphan " + orphan)
            if not check:
                _tree_prune(root, RULES_DIR, orphan)
        # The reference tree: the same full render at the same derived path, compared byte for byte (the
        # target's raw bytes against the render encoded as UTF-8, so a CRLF, a lone CR or a non-UTF-8 byte is
        # drift) and written as bytes beneath the descriptors the walk holds (_write_target: missing directories
        # made beneath the held descriptor, a new temporary file renamed over the target, never written through).
        for rel, content in sorted(desired.items()):
            want = content.encode("utf-8")
            if held[rel] != want:
                drift.append(REFERENCE_DIR + "/" + rel)
                if not check:
                    _write_target(root, REFERENCE_DIR + "/" + rel, want)
        # Every file in a reference family directory that no source renders, whatever its name, and every
        # directory there that holds no desired path, is an orphan, pruned beneath its parent's descriptor.
        for orphan in orphans:
            drift.append("orphan " + REFERENCE_DIR + "/" + orphan)
            if not check:
                _tree_prune(root, REFERENCE_DIR, orphan)
    except (OSError, UnicodeError, ComposeError) as exc:
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
    # Unknown-option rejection precedes every dispatch: main() defines only --check and --self-test, so any other
    # token (a misspelled --chek, a stray --help, a positional) is exit 2 with the usage line on stderr, never a
    # fall-through to a write-mode run.
    if unknown_args_refused(argv, "gen_rules.py [--check | --self-test]"):
        return 2
    if "--self-test" in argv:
        # The whole self-test runs inside the fixture git lifecycle, so the code under test (the generators the
        # class check launches, whose git ls-files reads strip GIT_* and so drop the scrub's config pins) reads no
        # caller HOME, XDG or system git configuration either.
        from _git_fixture_env import fixture_git_lifecycle
        with fixture_git_lifecycle():
            return self_test_main()
    return run(repo_root(), "--check" in argv)


# --- self-test ----------------------------------------------------------------------------------------
# The unknown-option vectors (argv, expected exit, the check flag each run call carries): an unknown option is
# exit 2 with stderr only the refusal (_gen_argv_refusal) and reaches no run; no argument and --check alone still
# reach run.
# main() is driven with run and repo_root stood in for, so a reverted guard shows as a call to the stand-in
# (a write-mode run of the real tree) and never touches a file.
_ARGV_CASES = ((["--chek"], 2, []), (["--check", "--chek"], 2, []), (["--help"], 2, []), (["check"], 2, []),
               ([], 0, [False]), (["--check"], 0, [True]))


def _argv_guard_failures(namespace, prog, run_name="run"):
    """One failure line per _ARGV_CASES vector namespace["main"] mishandles. namespace is a module's globals;
    its run_name and repo_root bindings are replaced for the calls and restored afterwards."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    calls, failures = [], []
    saved = namespace[run_name], namespace["repo_root"], sys.argv
    namespace[run_name] = lambda _root, check, *_rest: calls.append(check) or 0
    namespace["repo_root"] = lambda: None
    try:
        for argv, want, want_calls in _ARGV_CASES:
            del calls[:]
            err = io.StringIO()
            sys.argv = [prog] + argv
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                try:
                    got = namespace["main"]()
                except SystemExit as exc:
                    got = "raised SystemExit({!r})".format(exc.code)
            refused = _gen_argv_refusal(err.getvalue(), prog)
            if (got, calls, refused) != (want, want_calls, want == 2):
                failures.append("{} {}: expected exit {} with run calls {} and {}; got exit {}, run calls {}, "
                                "stderr {!r}".format(prog, " ".join(argv) or "(no argument)", want, want_calls,
                                                     "stderr only the refusal" if want == 2 else "no refusal",
                                                     got, calls, err.getvalue()[-200:]))
    finally:
        namespace[run_name], namespace["repo_root"], sys.argv = saved
    return failures


def _valued_option_failures():
    """One failure line per vector where _gen_common.unknown_args_refused mishandles a valued option: a following
    token that starts with "-" is never taken as its value (red when that rule is reverted)."""
    import io
    from contextlib import redirect_stderr
    failures = []
    usage = "gen_x.py [--check] [--root DIR]"
    for argv, want in ((["--root", "--chek"], True), (["--root", "-x"], True), (["--check", "--root"], True),
                       (["--root", "dir", "--check"], False), (["--check", "--root", "dir"], False)):
        err = io.StringIO()
        with redirect_stderr(err):
            got = unknown_args_refused(argv, usage, valued=("--root",))
        lines = err.getvalue().splitlines()
        if got != want or (lines[-1:] != ["usage: " + usage] if want else lines):
            failures.append("valued option {}: expected {}; got refused={}, stderr {!r}".format(
                " ".join(argv), "a refusal with the usage line" if want else "acceptance with no output", got,
                err.getvalue()[-200:]))
    return failures


# The class check: EVERY tools/gen_*.py, enumerated at run time, refuses each _GEN_ARGV_CLASS_CASES argv with exit
# 2, and its stderr is the refusal and nothing else (_gen_argv_refusal, the one refusal predicate): an optional
# `error:` line, then a last line that starts with `usage: <its name>`. An exit 2 for another reason (a different
# error, a usage text inside some other line, a line after the usage line) is reported. Each generator runs as
# __main__ in a child whose audit hook ends the child (exit 97) before a Python-level open for writing, a delete,
# rename, link, directory creation, os.mkfifo (which raises no audit event of its own, so the child wraps it to
# raise one) or a child process, so a generator whose guard is reverted shows as exit 97 (or a non-2 exit).
# The same check then confirms the defined modes still run, each in its own child under the same hook, which here
# lets two kinds of child process through. The first is the exact git index read the generators make, `git -C
# <dir>`, then only the pins `-c core.fsmonitor=false` and `-c protocol.allow=never`, then `ls-files` with only -z,
# --cached and --stage before an optional `--` and its pathspecs, launched as git with no GIT_* variable in its
# environment but GIT_NO_LAZY_FETCH. The second is the read tools/gen_rules_core.py makes of its budget at the
# merge base with main: `git -C <dir>`, then exactly the pins `-c gc.auto=0 -c gc.autoDetach=false -c
# maintenance.auto=false -c core.commitGraph=false -c protocol.allow=never`, then `rev-parse`, `merge-base` or
# `ls-tree`, or `cat-file blob <hex id>`, launched as git whose GIT_* variables are exactly the seven that read
# sets, each at its pinned value (GIT_CONFIG_GLOBAL and GIT_GRAFT_FILE the null device, GIT_CONFIG_NOSYSTEM,
# GIT_NO_LAZY_FETCH and GIT_NO_REPLACE_OBJECTS 1, GIT_OPTIONAL_LOCKS and GIT_TERMINAL_PROMPT 0), so a read with one
# left out or set otherwise is blocked. rev-parse, merge-base and ls-tree write nothing whatever their arguments,
# which are not judged; cat-file is held to that one form, so no --textconv or --filters driver runs. Its controls
# (_gen_argv_base_read_failures): a generator making such a read passes, the same read with the allowance switched
# off is reported, a generator running `update-ref` behind the same pins is reported, its ref never made, and one
# with GIT_NO_LAZY_FETCH at 0, GIT_CONFIG_GLOBAL naming a file, GIT_NO_REPLACE_OBJECTS left out or the protocol pin
# left off is reported, each passing with that value or pin check switched off. Any other git command is blocked.
# A write
# here is an open for writing, a directory creation, a delete, a rename or a truncation whose path lies in the
# child's working directory (the copy); the hook marks such a block, so a process event, an os.chmod, or a
# directory tempfile.mkdtemp makes outside the copy is not a write. On the current tree, with no argument the
# generator must end at a write or exit 0 with nothing blocked and no refusal, and with --check it must exit 0 or 1,
# not refused and with nothing blocked. On the stale tree (below), with no argument it must end at a write, and with
# --check it must exit 1 with nothing blocked and no refusal. So a generator that exits 2 with an `error:` line and
# a usage line whatever its arguments, which the refusal shape alone would pass, is reported, and so are one that
# runs a mode writing nothing with no argument and one whose --check writes.
# The check over the real generators is hermetic: they run only inside a throwaway git repository that
# _gen_argv_head_copy builds from `git archive HEAD` (extracted, then git init, add and commit there, on top of a
# first commit holding `git archive` of HEAD's first parent where HEAD has one, so tools/gen_rules_core.py --check
# there compares the budget log with that parent's log; _gen_argv_first_parent_failures shows it), each child
# with the copy as its working directory and an environment holding no GIT_* variable of the caller
# (_git_fixture_env.git_fixture_env). An uncommitted or stale file in the developer's working tree therefore cannot
# fail the check, and a generator's write that resolves its path from its own location or working directory, even
# one the hook does not see, lands in the copy, never in the developer's working tree.
# Plain form: in the copy each generator also runs as `python3 tools/<name> --chek` (the form the gensrc regenerate
# commands use: no -I, no -B, no hook) and must refuse it with exit 2 and stderr only the refusal. After each
# generator's runs the copy's `git status --ignored` must show nothing new but .pyc files directly in a
# __pycache__/ directory, so the plain form also writes nothing but Python's own bytecode cache, which the imports
# write before the guard runs (gitignored).
# Stale tree: the copy's generated files are current, so a generator that writes only when a target differs (not
# every generator does: reconcile in _gen_common writes whenever it is not in check mode) would write nothing there
# even if it wrote before its guard or ran the wrong mode. So, in the copy, the refusal cases, the plain form, the
# no-argument case and the --check case each run on a tree made stale on purpose: every file under each target the
# copy's
# .aiqt/gensrc.json names for that generator (its entry's regenerate command `python3 tools/<name>`) gains a stale
# line at its end and after each line holding BEGIN (inside a marked block, for a block target), and after the
# run it must still hold exactly those bytes; the originals are then restored. So a write on difference before the
# guard is blocked by the hook (the refusal cases, the --check case), or rewrites the altered file (the plain
# form), and is reported; a no-argument run that writes nothing on the stale tree is reported too. A generator the
# registry names no target for is reported, since its refusal ran on no stale target, and its no-argument and
# --check cases then run on the current tree only.
# Residuals, not covered:
# - the check reads the committed tree: an uncommitted change to a generator is checked once it is committed, and
#   a stale generated file committed at HEAD does not fail it, since on the current tree a --check run passes with
#   exit 1 as with exit 0 and a no-argument run passes by reaching a write (the --check gates fail that commit);
# - the mode cases show behaviour, not the mode: a no-argument run passes by reaching any write in the copy (the
#   hook ends it there, so what it would have written is not seen), and a --check run passes by exiting 1 with
#   nothing blocked on the stale tree, whatever made it exit 1; a path relative to a dir_fd passed to os.open is
#   judged against the working directory;
# - the stale tree alters only the files the registry declares: a write before the guard to a file the registry
#   does not name, or to a block whose opening marker line does not hold BEGIN, is still not made to fire; it adds
#   no file to a directory target, so a deletion of an orphan file before the guard is not made to fire either;
# - a write made inside C code without an audit event (for example the database file sqlite3 creates, os.mknod,
#   posix.mkfifo, which the os.mkfifo wrapper does not reach, or an extension module's own I/O) is not seen by the
#   hook, and the status comparison sees it only when it changes a file in the copy: such a write of identical
#   bytes, or one to a path outside the copy, would pass this check; so would such a write by an allowed git read;
# - the copy is read from the real checkout by `rev-parse HEAD^@` and `git archive` (of HEAD's first parent, where
#   there is one, and of HEAD) in the fixture environment (no caller HOME, XDG or system configuration is read),
#   each with one command-line safe.directory entry naming that checkout's resolved path, so a checkout owned by
#   another uid is still copied (_gen_argv_head_copy); that entry lets git read the checkout whatever the caller's
#   own safe.directory setting says. The copy holds two commits at most, so a history read further back than
#   HEAD's first parent sees nothing there. What the rows show: the entry is the only one, it is on the reads of
#   the checkout alone, it names that resolved path (the foreign-owner row,
#   _gen_argv_foreign_owner_failures, which stands in for the other uid with git's GIT_TEST_ASSUME_DIFFERENT_OWNER
#   switch, so a git that ignores that switch is reported, not passed), and a resolved path that git would read as
#   a pattern (one holding `*`, such as one ending in `/*`) or not as a plain absolute path is refused before any
#   git call (_gen_argv_safe_directory, the glob-path row _gen_argv_glob_root_failures). What they do not show:
#   git matches the entry against the worktree path, while its ownership check also covers the git directory, so
#   where that git directory has an owner of its own (a linked worktree's, or a .git owned apart from the
#   worktree) the entry may also admit it and the repository configuration git archive reads from it; that this
#   trust is no wider than the trust already placed in the self-test's own code is not established here;
# - only files matching gen_*.py directly in tools/ are enumerated, so a generator nested in a subdirectory or
#   named otherwise is not checked.
_GEN_ARGV_CLASS_CASES = (["--chek"], ["--check", "--chek"], ["--self-test", "--chek"], ["--help"], ["check"],
                         ["--root", "--chek"])
# The text the hook below adds to a block that is a write in its working directory (_WRITE_PATH there); a mode case
# shows a write only when its child was ended at a block carrying it.
_GEN_ARGV_TREE_WRITE = " (a write in the working directory)"
_GEN_ARGV_CHILD = """import contextlib, io, json, os, runpy, sys
_real_mkfifo = os.mkfifo
def _audited_mkfifo(path, mode=0o666, *, dir_fd=None):
    sys.audit("os.mkfifo", path, mode, dir_fd)
    return _real_mkfifo(path, mode, dir_fd=dir_fd)
os.mkfifo = _audited_mkfifo
_MUTATING = frozenset(("os.chflags", "os.chmod", "os.chown", "os.exec", "os.fork", "os.forkpty", "os.kill",
                       "os.killpg", "os.lchflags", "os.link", "os.mkdir", "os.mkfifo", "os.posix_spawn", "os.remove",
                       "os.removexattr", "os.rename", "os.rmdir", "os.setxattr", "os.spawn", "os.symlink",
                       "os.system", "os.truncate", "os.utime", "shutil.chown", "shutil.copyfile", "shutil.copymode",
                       "shutil.copystat", "shutil.copytree", "shutil.make_archive", "shutil.move", "shutil.rmtree",
                       "subprocess.Popen", "tempfile.mkdtemp", "tempfile.mkstemp"))
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
script, cases, accept = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3] == "accept"
_CWD = os.getcwd()
# event: (index of the written path in its arguments, index of its dir_fd or None)
_WRITE_PATH = {"open": (0, None), "os.mkdir": (0, 2), "os.remove": (0, 1), "os.rename": (1, 3),
               "os.truncate": (0, None)}
def _in_tree(event, args):
    if event not in _WRITE_PATH:
        return False
    i, fd = _WRITE_PATH[event]
    try:
        path = os.fsdecode(os.fspath(args[i]))
        if fd is not None and len(args) > fd and args[fd] is not None and not os.path.isabs(path):
            return False
        path = os.path.normpath(os.path.join(_CWD, path))
    except (TypeError, ValueError, IndexError):
        return False
    return path == _CWD or path.startswith(_CWD.rstrip(os.sep) + os.sep)
def _git_listing(args):
    if not accept or len(args) < 4 or not isinstance(args[1], (list, tuple)):
        return False
    try:
        exe = None if args[0] is None else os.fsdecode(args[0])
        argv = [os.fsdecode(a) for a in args[1]]
        env = [os.fsdecode(k) for k in (os.environ if args[3] is None else args[3])]
    except TypeError:
        return False
    if (exe is not None and os.path.basename(exe) != "git") or len(argv) < 4 or os.path.basename(argv[0]) != "git":
        return False
    if argv[1] != "-C" or any(k.startswith("GIT_") and k != "GIT_NO_LAZY_FETCH" for k in env):
        return False
    i = 3
    while argv[i:i + 1] == ["-c"] and argv[i + 1:i + 2] in (["core.fsmonitor=false"], ["protocol.allow=never"]):
        i += 2
    if argv[i:i + 1] != ["ls-files"]:
        return False
    rest = argv[i + 1:]
    return all(tok in ("-z", "--cached", "--stage") for tok in (rest[:rest.index("--")] if "--" in rest else rest))
_BASE_READS_ON = True
_BASE_PROTOCOL_PINNED = True
_BASE_ENV_PINNED = True
_BASE_PINS = ["-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false", "-c",
              "core.commitGraph=false", "-c", "protocol.allow=never"]
_BASE_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_GRAFT_FILE": os.devnull,
             "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0",
             "GIT_TERMINAL_PROMPT": "0"}
def _git_base_read(args):
    if not (_BASE_READS_ON and accept) or len(args) < 4 or not isinstance(args[1], (list, tuple)):
        return False
    try:
        exe = None if args[0] is None else os.fsdecode(args[0])
        argv = [os.fsdecode(a) for a in args[1]]
        source = os.environ if args[3] is None else args[3]
        git_env = {os.fsdecode(k): os.fsdecode(source[k]) for k in source if os.fsdecode(k).startswith("GIT_")}
    except (TypeError, KeyError, AttributeError):
        return False
    pins = _BASE_PINS if _BASE_PROTOCOL_PINNED else _BASE_PINS[:8]
    if (exe is not None and os.path.basename(exe) != "git") or len(argv) < 4 + len(pins) \\
            or os.path.basename(argv[0]) != "git":
        return False
    if _BASE_ENV_PINNED:
        env_ok = git_env == _BASE_ENV
    else:
        env_ok = all(k in _BASE_ENV for k in git_env)
    if argv[1] != "-C" or argv[3:3 + len(pins)] != pins or not env_ok:
        return False
    sub, rest = argv[3 + len(pins)], argv[4 + len(pins):]
    if sub == "cat-file":
        return len(rest) == 2 and rest[0] == "blob" and rest[1] != "" and set(rest[1]) <= set("0123456789abcdef")
    return sub in ("rev-parse", "merge-base", "ls-tree")
def _hook(event, args):
    if event == "open":
        mode, flags = args[1], args[2]
        writes = isinstance(flags, int) and flags & _WRITE_FLAGS
        if not (writes or (isinstance(mode, str) and set(mode) & set("wax+"))):
            return
    elif event not in _MUTATING or (event == "subprocess.Popen" and (_git_listing(args) or _git_base_read(args))):
        return
    note = " (a write in the working directory)" if _in_tree(event, args) else ""
    os.write(2, ("blocked: " + event + note + "\\n").encode())
    os._exit(97)
sys.addaudithook(_hook)
for argv in cases:
    sys.argv = [script] + argv
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        try:
            runpy.run_path(script, run_name="__main__")
            code = "returned without exiting"
        except SystemExit as exc:
            code = exc.code
    print(json.dumps([argv, code, err.getvalue()]), flush=True)
"""


def _gen_argv_child(path, cases, accept, cwd):
    """Run _GEN_ARGV_CHILD over path for cases in the directory cwd, with no GIT_* variable of the caller in its
    environment: (child exit, {argv tuple: (exit, stderr)}, child stderr text), or a failure line when the child did
    not run."""
    import json
    import subprocess
    from _git_fixture_env import git_fixture_env
    try:
        done = subprocess.run([sys.executable, "-I", "-B", "-c", _GEN_ARGV_CHILD, str(path), json.dumps(cases),
                               "accept" if accept else "refuse"], capture_output=True, timeout=300, cwd=str(cwd),
                              env=git_fixture_env())
    except (OSError, subprocess.SubprocessError) as exc:
        return "{}: the argument child did not run: {}".format(path.name, exc)
    try:
        rows = [json.loads(line) for line in done.stdout.decode("utf-8").splitlines()]
    except ValueError as exc:
        rows = [["(unreadable child output)", repr(exc), ""]]
    seen = {tuple(argv): (code, err) for argv, code, err in rows if isinstance(argv, list)}
    return done.returncode, seen, done.stderr.decode("utf-8", "replace")


def _gen_argv_class_failures(tools, repo=None):
    """One failure line per tools/gen_*.py in the directory tools that does not refuse every
    _GEN_ARGV_CLASS_CASES argv with exit 2 and a stderr that is only the refusal (see _gen_argv_refusal), or whose
    no-argument or --check run fails _gen_argv_mode_failures on the current tree. With repo, a throwaway git
    repository holding tools as repo/tools, the children run in repo, each generator also runs in the plain form
    `python3 tools/<name> --chek` there, a change to repo that `git status --ignored` shows after a generator's
    runs, other than a .pyc file in a __pycache__/ directory, is reported (see _gen_argv_plain_failures), the
    refusal cases and the plain form run on a tree made stale on purpose (see _gen_argv_stale), and the
    no-argument and --check runs are repeated on that stale tree (_gen_argv_mode_failures with stale true)."""
    failures = []
    cwd = repo if repo is not None else tools
    changed = _gen_argv_status(repo) if repo is not None else None
    if isinstance(changed, str):
        return [changed]
    registry = _gen_argv_registry(repo) if repo is not None else None
    if isinstance(registry, str):
        return [registry]
    for path in sorted(Path(tools).glob("gen_*.py")):
        targets = () if registry is None else registry.get(path.name, ())
        if registry is not None and not targets:
            failures.append("{}: {} names no target it regenerates, so its refusal cases ran on no stale "
                            "target".format(path.name, _GEN_ARGV_REGISTRY))
        stale = _gen_argv_stale(repo, targets) if targets else {}
        if isinstance(stale, str):
            failures.append(stale)
            stale = {}
        got = _gen_argv_child(path, _GEN_ARGV_CLASS_CASES, False, cwd)
        restored = _gen_argv_restore(stale, repo, "{} (refusal cases)".format(path.name))
        if isinstance(got, str):
            failures.append(got)
        else:
            returncode, seen, child_err = got
            for argv in _GEN_ARGV_CLASS_CASES:
                code, err = seen.get(tuple(argv), ("no result (child exit {})".format(returncode), ""))
                if code != 2 or not _gen_argv_refusal(err, path.name):
                    failures.append("{} {}: expected exit 2 with stderr only the refusal (an optional error: line, "
                                    "then a last line starting usage: {}) and nothing written; got exit {}, stderr "
                                    "{!r}, child stderr {!r}".format(path.name, " ".join(argv), path.name, code,
                                                                     err[-200:], child_err[-200:]))
            if returncode != 0:
                failures.append("{}: the unknown-option child exited {} (97 is a blocked write, delete or child "
                                "process); stderr {!r}".format(path.name, returncode, child_err[-300:]))
        failures.extend(restored)
        # The no-argument and --check cases on the current tree, then (for a generator with registry targets) on
        # the stale tree, restored afterwards.
        failures.extend(_gen_argv_mode_failures(path, cwd, False))
        if targets:
            stale = _gen_argv_stale(repo, targets)
            if isinstance(stale, str):
                failures.append(stale)
            else:
                failures.extend(_gen_argv_mode_failures(path, cwd, True))
                failures.extend(_gen_argv_restore(stale, repo, "{} (no argument and --check on the stale tree)".format(
                    path.name)))
        if repo is not None:
            failures.extend(_gen_argv_plain_failures(path, repo, changed, targets))
    return failures


def _gen_argv_mode_failures(path, cwd, stale):
    """The no-argument and --check cases for the generator path, run in cwd under the hook: one failure line per
    case it fails. With no argument the child must end at a write in its working directory (see
    _GEN_ARGV_TREE_WRITE), or, on the current tree (stale false) only, exit 0 with nothing blocked and no refusal.
    With --check it must exit 1 on the stale tree, or 0 or 1 on the current tree, with nothing blocked and no
    refusal."""
    failures = []
    where = " on the stale tree" if stale else ""
    got = _gen_argv_child(path, [[]], True, cwd)
    if isinstance(got, str):
        failures.append(got)
    else:
        returncode, seen, child_err = got
        blocked = [line[len("blocked: "):] for line in child_err.splitlines() if line.startswith("blocked: ")]
        code, err = seen.get((), ("no result (child exit {})".format(returncode), ""))
        wrote = returncode == 97 and bool(blocked) and blocked[-1].endswith(_GEN_ARGV_TREE_WRITE)
        current = not stale and returncode == 0 and code == 0 and not _gen_argv_refusal(err, path.name)
        if not (wrote or current):
            failures.append("{} (no argument){}: expected write mode, ended by the hook at a write in its working "
                            "directory{}; got child exit {}, blocked {}, exit {}, stderr {!r}, child stderr "
                            "{!r}".format(path.name, where, "" if stale else " or (nothing to write) exit 0 with "
                                          "nothing blocked and no refusal", returncode, blocked, code, err[-200:],
                                          child_err[-200:]))
    got = _gen_argv_child(path, [["--check"]], True, cwd)
    if isinstance(got, str):
        failures.append(got)
    else:
        returncode, seen, child_err = got
        code, err = seen.get(("--check",), ("no result (child exit {})".format(returncode), ""))
        want = (1,) if stale else (0, 1)
        if returncode != 0 or code not in want or _gen_argv_refusal(err, path.name):
            failures.append("{} --check{}: expected check mode, exit {} with nothing written and no refusal; got "
                            "child exit {} (97 is a blocked write, delete or child process), exit {}, stderr {!r}, "
                            "child stderr {!r}".format(path.name, where, " or ".join(str(w) for w in want),
                                                       returncode, code, err[-200:], child_err[-200:]))
    return failures


# The registry the stale tree reads, and the line _gen_argv_stale adds to each file it alters.
_GEN_ARGV_REGISTRY = ".aiqt/gensrc.json"
_GEN_ARGV_STALE_LINE = b"stale: the gen_rules class check altered this generated file\n"


def _gen_argv_registry(repo):
    """{generator file name: [target, ...]} from repo's .aiqt/gensrc.json, each entry filed under the generator
    its regenerate command names (`python3 tools/<name>`); None when repo has no registry; a failure line when it
    cannot be read."""
    import json
    path = Path(repo) / _GEN_ARGV_REGISTRY
    if not os.path.lexists(path):
        return None
    registry = {}
    try:
        for entry in json.loads(path.read_text(encoding="utf-8"))["generated"]:
            words = entry["regenerate"].split()
            if len(words) >= 2 and words[1].startswith("tools/") and isinstance(entry["target"], str):
                registry.setdefault(words[1][len("tools/"):], []).append(entry["target"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return "class check: cannot read {} in the copy: {!r}".format(_GEN_ARGV_REGISTRY, exc)
    return registry


def _gen_argv_stale(repo, targets):
    """Make the targets (repo-relative registry targets: a file, or a tree ending in /) stale in repo: every regular
    file there (a symlink is left alone; a missing target is already stale) gains _GEN_ARGV_STALE_LINE after each
    line holding BEGIN, so a marked block differs, and at its end, so a whole file differs. Return {file: (original
    bytes, altered bytes)} for _gen_argv_restore, or a failure line when a file could not be altered (those already
    altered are restored first)."""
    stale = {}
    try:
        for rel in targets:
            top = Path(repo) / rel.rstrip("/")
            if top.is_dir() and not top.is_symlink():
                files = sorted(Path(base) / name for base, _dirs, names in os.walk(top) for name in names)
            else:
                files = [top]
            for path in files:
                if path.is_symlink() or not path.is_file() or path in stale:
                    continue
                data = path.read_bytes()
                out = []
                for line in data.splitlines(keepends=True):
                    out.append(line if line.endswith(b"\n") else line + b"\n")
                    if b"BEGIN" in line:
                        out.append(_GEN_ARGV_STALE_LINE)
                altered = b"".join(out) + _GEN_ARGV_STALE_LINE
                stale[path] = (data, altered)
                path.write_bytes(altered)
    except OSError as exc:
        problems = _gen_argv_restore(stale, repo, "class check")
        return "class check: could not make {} stale in the copy: {}{}".format(
            ", ".join(targets), exc, "; " + "; ".join(problems) if problems else "")
    return stale


def _gen_argv_restore(stale, repo, label):
    """Restore every file _gen_argv_stale altered to its original bytes. Return one failure line per file that no
    longer held its altered bytes (the run under label rewrote, removed or replaced it) or could not be restored."""
    failures = []
    for path, (data, altered) in stale.items():
        rel = path.relative_to(repo).as_posix()
        try:
            held = None if path.is_symlink() or not path.is_file() else path.read_bytes()
        except OSError as exc:
            held = "unreadable ({})".format(exc)
        if held != altered:
            failures.append("{}: the run changed the generated file {}, which the class check had made stale (a "
                            "write before the argument guard)".format(label, rel))
        try:
            if path.is_symlink() or (os.path.lexists(path) and not path.is_file()):
                failures.append("{}: cannot restore {}: it is no longer a regular file".format(label, rel))
                continue
            path.write_bytes(data)
        except OSError as exc:
            failures.append("{}: cannot restore {}: {}".format(label, rel, exc))
    return failures


def _gen_argv_git(cwd, *args, **overrides):
    """Run git in cwd with the fixture environment (_git_fixture_env.git_fixture_env: no GIT_* variable of the
    caller, a scratch HOME, global and system configuration pinned to os.devnull) and pinned options. overrides
    are git_fixture_env's keyword overrides (only the foreign-owner row passes one)."""
    import subprocess
    from _git_fixture_env import git_fixture_env
    return subprocess.run(["git", "-C", str(cwd), "-c", "core.fsmonitor=false", "-c", "core.attributesFile=/dev/null",
                           "-c", "user.name=aiqt-selftest", "-c", "user.email=selftest@invalid",
                           "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
                          capture_output=True, timeout=300, env=git_fixture_env(**overrides))


def _gen_argv_status(repo):
    """The set of `git status --porcelain -z --ignored --untracked-files=all` entries of repo, or a failure line."""
    done = _gen_argv_git(repo, "status", "--porcelain", "-z", "--ignored", "--untracked-files=all")
    if done.returncode != 0:
        return "class check: git status failed in the copy (exit {}): {!r}".format(
            done.returncode, done.stderr.decode("utf-8", "replace")[-300:])
    return {entry for entry in done.stdout.decode("utf-8", "replace").split("\0") if entry}


def _gen_argv_plain_failures(path, repo, changed, targets=()):
    """The plain-form case for the generator path in the throwaway repository repo: `python3 tools/<name> --chek`
    with repo as the working directory, no -I, no -B and no hook, must exit 2 with stderr only the refusal, on a
    tree whose registry targets (targets) are made stale for the run and must still be stale after it
    (_gen_argv_stale, _gen_argv_restore); then every entry `git status --ignored` shows in repo that is not in the
    set changed and is not a .pyc file directly in a __pycache__/ directory is reported as written by this
    generator's runs. changed gains the entries seen."""
    import subprocess
    from _git_fixture_env import git_fixture_env
    failures = []
    rel = path.relative_to(repo).as_posix()
    stale = _gen_argv_stale(repo, targets) if targets else {}
    if isinstance(stale, str):
        failures.append(stale)
        stale = {}
    try:
        done = subprocess.run([sys.executable, rel, "--chek"], capture_output=True, timeout=300, cwd=str(repo),
                              env=git_fixture_env())
        err = done.stderr.decode("utf-8", "replace")
        if done.returncode != 2 or not _gen_argv_refusal(err, path.name):
            failures.append("{} --chek (plain form python3 {} --chek): expected exit 2 with stderr only the refusal; "
                            "got exit {}, stderr {!r}".format(path.name, rel, done.returncode, err[-200:]))
    except (OSError, subprocess.SubprocessError) as exc:
        failures.append("{}: the plain-form run did not run: {}".format(path.name, exc))
    failures.extend(_gen_argv_restore(stale, repo, "{} --chek (plain form)".format(path.name)))
    now = _gen_argv_status(repo)
    if isinstance(now, str):
        return failures + [now]
    new = sorted(entry for entry in now - changed if not _gen_argv_bytecode(entry[3:]))
    changed |= now
    if new:
        failures.append("{}: its runs changed the copy (git status entries other than .pyc files in "
                        "__pycache__/): {!r}".format(path.name, new[:10]))
    return failures


def _gen_argv_bytecode(rel):
    """True when the repo-relative path rel is Python's bytecode cache: a .pyc file directly in a __pycache__/
    directory. Any other file, under __pycache__/ or not, is a change the status comparison reports."""
    parts = rel.split("/")
    return len(parts) >= 2 and parts[-2] == "__pycache__" and parts[-1].endswith(".pyc")


def _gen_argv_safe_directory(root):
    """(value, None): the safe.directory value naming root alone, root's resolved path; or (None, failure line)
    when git would not read that path as itself. git reads a safe.directory value as more than a path: `*` trusts
    every repository, a value ending in `/*` trusts every path under its prefix, `.` and any other value that is
    not absolute name no fixed path, and a value starting `~` or `%(prefix)/` is expanded first. So a resolved
    path that does not start with `/` (no leading character git rewrites) or that holds `*` anywhere is refused,
    failing closed."""
    resolved = str(Path(root).resolve())
    if not resolved.startswith("/") or "*" in resolved:
        return None, ("class check: built no copy, the checkout path {!r} cannot be named alone in a safe.directory "
                      "entry: git reads a value holding `*` (such as one ending in `/*`) as a pattern and a value not "
                      "starting with `/` as no plain absolute path; run the check from a checkout whose resolved "
                      "path is absolute and holds no `*`".format(resolved))
    return resolved, None


def _gen_argv_first_parent(root, entry, **overrides):
    """(parent, None): the first parent of root's HEAD, or None when HEAD is a root commit; or (None, failure line).
    Read with `rev-parse HEAD^@` (every parent, first parent first) under the safe.directory entry for root, with
    replacement refs ignored (--no-replace-objects and GIT_NO_REPLACE_OBJECTS=1), the graft file pinned to the null
    device and the commit-graph off, so none of them changes which parent is read. A name-keyed function, so the
    self-test can switch it off (the flip: the copy is one commit again)."""
    done = _gen_argv_git(root, "-c", "safe.directory=" + entry, "-c", "core.commitGraph=false", "--no-replace-objects",
                         "rev-parse", "HEAD^@",
                         **dict(overrides, GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull))
    if done.returncode != 0:
        return None, "class check: built no copy, the parents of HEAD of {} cannot be read (exit {}): {!r}".format(
            root, done.returncode, done.stderr.decode("utf-8", "replace")[-300:])
    parents = done.stdout.decode("ascii", "replace").split()
    if not parents:
        return None, None
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", parents[0]) is None:
        return None, "class check: built no copy, HEAD of {} names a malformed first parent {!r}".format(
            root, parents[0])
    return parents[0], None


def _gen_argv_extract(root, entry, rev, dest, **overrides):
    """`git archive <rev>` of root (under the safe.directory entry for root, replacement refs ignored), extracted
    into dest: None, or a failure line."""
    import tarfile
    tar = dest.parent / (dest.name + ".tar")
    done = _gen_argv_git(root, "-c", "safe.directory=" + entry, "archive", "--format=tar", "-o",
                         str(tar), rev, **overrides)
    if done.returncode != 0:
        return "class check: built no copy, git archive {} of {} failed (exit {}): {!r}".format(
            "HEAD" if rev == "HEAD" else rev[:12], root, done.returncode,
            done.stderr.decode("utf-8", "replace")[-300:])
    try:
        with tarfile.open(str(tar), mode="r:") as archive:
            if hasattr(tarfile, "data_filter"):
                archive.extractall(str(dest), filter="data")
            else:
                archive.extractall(str(dest))
    except (OSError, tarfile.TarError) as exc:
        return "class check: built no copy, extracting the {} archive failed: {}".format(
            "HEAD" if rev == "HEAD" else "first-parent", exc)
    finally:
        tar.unlink()
    return None


def _gen_argv_head_copy(root, dest, **overrides):
    """Build at dest (which must not exist) a throwaway git repository holding the committed tree of root's HEAD:
    `git archive HEAD` of root, extracted into dest, then git init, add and commit there. Where root's HEAD has a
    first parent (_gen_argv_first_parent), `git archive` of that parent is extracted and committed first, on main,
    and HEAD's tree is committed on top of it, so the copy's HEAD has a first parent holding the tree root's HEAD's
    first parent holds: tools/gen_rules_core.py --check in the copy (HEAD on main, so its merge base with main is
    HEAD) then compares the budget log with that parent's log. A root commit gives a one-commit copy, as in root.
    The copy holds those two commits only. Return None, or a failure line when the copy could not be built.
    The parent read and the archive reads are the git calls here on a checkout this self-test did not create. They
    keep the fixture environment, so no caller or system configuration is read, and carry exactly one command-line
    safe.directory entry, naming root and nothing else, so a root owned by another uid is still read (git honours
    safe.directory given on the command line). A root whose resolved path git would read as a pattern is refused
    before dest is made or git runs (_gen_argv_safe_directory). The fixture calls in dest carry no such entry.
    overrides go to the reads of root only (the foreign-owner row)."""
    import shutil
    entry, problem = _gen_argv_safe_directory(root)
    if problem:
        return problem
    parent, problem = _gen_argv_first_parent(root, entry, **overrides)
    if problem:
        return problem
    dest.mkdir()
    if parent is not None:
        problem = (_gen_argv_extract(root, entry, parent, dest, **dict(overrides, GIT_NO_REPLACE_OBJECTS="1"))
                   or _gen_argv_commit(dest))
        if problem:
            return problem
        try:
            for child in dest.iterdir():
                if child.name == ".git":
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        except OSError as exc:
            return "class check: built no copy, clearing the first parent's tree failed: {}".format(exc)
    return _gen_argv_extract(root, entry, "HEAD", dest, **overrides) or _gen_argv_commit(dest)


def _gen_argv_commit(dest):
    """git init, add (every file, ignored ones too) and commit in dest: None, or a failure line."""
    for args in (("init", "-q"), ("add", "-A", "-f"), ("commit", "-q", "--allow-empty", "-m", "class check copy")):
        done = _gen_argv_git(dest, *args)
        if done.returncode != 0:
            return "class check: built no copy, git {} failed in {} (exit {}): {!r}".format(
                args[0], dest, done.returncode, done.stderr.decode("utf-8", "replace")[-300:])
    return None


# The foreign-owner row: the class check's read of the real checkout must work on a checkout owned by another uid
# (git refuses such a checkout as of dubious ownership unless protected configuration names it in safe.directory,
# and the fixture environment pins away the caller's global configuration where that entry usually lives). The
# self-test cannot change a file's owner, so git's own test switch GIT_TEST_ASSUME_DIFFERENT_OWNER=1 (git's ownership
# check then treats the checkout as another uid's) stands in, given to the reads of the checkout only. A control
# shows the switch bites on this git: the archive read with no safe.directory entry must be refused for dubious
# ownership, so a git that ignores the switch is reported rather than passed. Then _gen_argv_head_copy must build the
# copy (red when its safe.directory entry is removed), with its two reads of the checkout first (the parent read,
# then the archive of HEAD; the checkout is one commit, so no first-parent archive), each with exactly one
# safe.directory entry, naming that checkout, and the owner switch, and none on any fixture call.
_GEN_ARGV_FOREIGN_OWNER = {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}


def _gen_argv_foreign_owner_failures():
    """The foreign-owner row (see the comment above): one failure line per broken expectation."""
    import shutil
    import tempfile
    failures = []
    real = _gen_argv_git
    calls = []

    def recording(cwd, *args, **overrides):
        calls.append((Path(cwd), args, overrides))
        return real(cwd, *args, **overrides)

    tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-argv-owner-"))
    try:
        source = tmp / "checkout"
        source.mkdir()
        (source / "seed.txt").write_text("seed\n", encoding="utf-8")
        problem = _gen_argv_commit(source)
        if problem:
            return ["foreign-owner row: " + problem]
        trust = "safe.directory=" + str(source.resolve())
        control = real(source, "archive", "--format=tar", "-o", str(tmp / "control.tar"), "HEAD",
                       **_GEN_ARGV_FOREIGN_OWNER)
        control_err = control.stderr.decode("utf-8", "replace")
        if control.returncode == 0 or "dubious ownership" not in control_err:
            failures.append("foreign-owner control: under {} the archive read with no safe.directory entry must be "
                            "refused for dubious ownership, or the row proves nothing; got exit {}, stderr "
                            "{!r}".format(_GEN_ARGV_FOREIGN_OWNER, control.returncode, control_err[-300:]))
        copy = tmp / "copy"
        globals()["_gen_argv_git"] = recording
        try:
            got = _gen_argv_head_copy(source, copy, **_GEN_ARGV_FOREIGN_OWNER)
        finally:
            globals()["_gen_argv_git"] = real
        seed = copy / "seed.txt"
        if got is not None or not seed.is_file() or seed.read_text(encoding="utf-8") != "seed\n":
            failures.append("foreign-owner row: the copy of a checkout git treats as another uid's must be built "
                            "with its committed files; got {!r}, seed.txt {}".format(
                                got, "present" if seed.is_file() else "absent"))
        archives = [call for call in calls if "archive" in call[1]]
        parent_reads = [call for call in calls if "rev-parse" in call[1]]
        entries = [[arg for arg in args if arg.startswith("safe.directory")] for _cwd, args, _env in calls]
        expected = [[trust]] * 2 + [[]] * (len(calls) - 2)
        if (len(archives) != 1 or len(parent_reads) != 1 or calls[:2] != parent_reads + archives
                or archives[0][0] != source or parent_reads[0][0] != source
                or archives[0][2] != _GEN_ARGV_FOREIGN_OWNER
                or parent_reads[0][2] != dict(_GEN_ARGV_FOREIGN_OWNER, GIT_NO_REPLACE_OBJECTS="1",
                                              GIT_GRAFT_FILE=os.devnull)
                or entries != expected or any(overrides for _cwd, _args, overrides in calls[2:])):
            failures.append("foreign-owner row: expected the parent read, then the archive read, of {} first, each "
                            "with the one entry {!r} and the owner switch, then only fixture calls with no "
                            "safe.directory entry and no override; got {!r}".format(source, trust, calls))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


# The glob-path row: a checkout whose resolved path git would read as a pattern must not be read under an entry
# built from that path. A checkout in a directory named `*` (its path ends in `/*`, which git reads as a prefix match
# over every path under its parent) and one in a directory named `a*b` are each committed, then _gen_argv_head_copy
# under the owner switch must refuse with the safe.directory failure line, make no copy and run no git call (red
# when _gen_argv_safe_directory's refusal is removed: the entry then admits the checkout and the copy is built).
def _gen_argv_glob_root_failures():
    """The glob-path row (see the comment above): one failure line per broken expectation."""
    import shutil
    import tempfile
    failures = []
    real = _gen_argv_git
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-argv-glob-"))
    try:
        for parent, name in (("suffix", "*"), ("inner", "a*b")):
            source = tmp / parent / name
            source.mkdir(parents=True)
            (source / "seed.txt").write_text("seed\n", encoding="utf-8")
            problem = _gen_argv_commit(source)
            if problem:
                failures.append("glob-path row: " + problem)
                continue
            copy = tmp / parent / "copy"
            calls = []

            def recording(cwd, *args, **overrides):
                calls.append((Path(cwd), args, overrides))
                return real(cwd, *args, **overrides)

            globals()["_gen_argv_git"] = recording
            try:
                got = _gen_argv_head_copy(source, copy, **_GEN_ARGV_FOREIGN_OWNER)
            finally:
                globals()["_gen_argv_git"] = real
            if (not isinstance(got, str) or "cannot be named alone in a safe.directory entry" not in got
                    or os.path.lexists(copy) or calls):
                failures.append("glob-path row: a checkout at {} must be refused before any git call, with the "
                                "safe.directory failure line and no copy; got {!r}, copy {}, git calls {!r}".format(
                                    source, got, "present" if os.path.lexists(copy) else "absent", calls))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


# The first-parent rows: the class check's copy commits HEAD's first parent's tree under HEAD's tree, so
# tools/gen_rules_core.py --check in the copy (HEAD on main, so the merge base is HEAD) compares the budget log with
# the log that first parent holds, not with itself. The source is a copy of this checkout's HEAD with three commits
# on top: a shrink entry appended to the budget log (file-cap lowered by one), a clean change (a marker file), then
# the shrink entry deleted again (the undo). A copy of the source at the clean change must pass gen_rules_core.py
# --check (exit 0: its first parent holds the shrink it keeps) and a copy at the undo must fail it (exit 1: its first
# parent holds the entry it deletes). The flip switches _gen_argv_first_parent off, so the copy is one commit again,
# as before this change: both runs are then exit 2 (a root commit on main), so each row depends on the first parent
# being committed. What the rows do not show: a check reading further back than the first parent (the copy holds two
# commits only).
def _gen_argv_first_parent_failures():
    """The first-parent rows (see the comment above): one failure line per broken expectation."""
    import json
    import shutil
    import subprocess
    import tempfile
    import tomllib
    from _git_fixture_env import git_fixture_env
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-argv-parent-"))

    def core_check(copy):
        done = subprocess.run([sys.executable, "tools/gen_rules_core.py", "--check"], capture_output=True,
                              timeout=300, cwd=str(copy), env=git_fixture_env())
        return done.returncode, done.stderr.decode("utf-8", "replace")

    def copied(name):
        copy = tmp / name
        problem = _gen_argv_head_copy(source, copy)
        return (None, problem) if problem else core_check(copy)

    try:
        source = tmp / "source"
        problem = _gen_argv_head_copy(Path(__file__).resolve().parent.parent, source)
        if problem:
            return ["first-parent rows: " + problem]
        budget = source / ".aiqt" / "core" / "core-budget.toml"
        seed_text = budget.read_text(encoding="utf-8")
        # A budget log that does not parse (tomllib raises a BARE ValueError as well as TOMLDecodeError, and
        # RecursionError or MemoryError on a deep or huge body) or holds no accepted entry is a failure line,
        # never a pass.
        try:
            in_force = tomllib.loads(seed_text)["accepted"][-1]
        except (RecursionError, MemoryError, ValueError, KeyError, IndexError, TypeError) as exc:
            return ["first-parent rows: the budget log {} cannot be read ({}: {}); fail-closed".format(
                budget, type(exc).__name__, exc)]
        budget.write_text(seed_text + (
            '\n[[accepted]]\nkind = "shrink"\nnote = "self-test: file-cap lowered by one"\nentry-cap = {}\n'
            "trigger-cap = {}\nfile-cap = {}\npending = {}\n").format(
                in_force["entry-cap"], in_force["trigger-cap"], in_force["file-cap"] - 1,
                json.dumps(in_force["pending"])), encoding="utf-8")
        problem = _gen_argv_commit(source)
        (source / "first-parent-marker.txt").write_text("clean change\n", encoding="utf-8")
        problem = problem or _gen_argv_commit(source)
        if problem:
            return ["first-parent rows: " + problem]
        clean = copied("clean")
        if clean[0] != 0:
            failures.append("first-parent row (clean): a copy whose first parent holds the budget log it keeps must "
                            "pass gen_rules_core.py --check (exit 0); got {!r}".format((clean[0], clean[1][-300:])))
        saved = _gen_argv_first_parent
        globals()["_gen_argv_first_parent"] = lambda _root, _entry, **_overrides: (None, None)
        try:
            flip_clean = copied("clean-flip")
        finally:
            globals()["_gen_argv_first_parent"] = saved
        budget.write_text(seed_text, encoding="utf-8")
        problem = _gen_argv_commit(source)
        if problem:
            return failures + ["first-parent rows: " + problem]
        undo = copied("undo")
        if undo[0] != 1:
            failures.append("first-parent row (undo): a copy that deletes a shrink entry its first parent holds must "
                            "fail gen_rules_core.py --check (exit 1); got {!r}".format((undo[0], undo[1][-300:])))
        globals()["_gen_argv_first_parent"] = lambda _root, _entry, **_overrides: (None, None)
        try:
            flip_undo = copied("undo-flip")
        finally:
            globals()["_gen_argv_first_parent"] = saved
        for label, flipped in (("clean", flip_clean), ("undo", flip_undo)):
            if flipped[0] != 2 or "is a root commit" not in flipped[1]:
                failures.append("first-parent flip ({}): with the first parent not committed the copy is one "
                                "commit, so gen_rules_core.py --check must exit 2 (a root commit on main); got "
                                "{!r}".format(label, (flipped[0], flipped[1][-300:])))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        failures.append("first-parent rows: the fixture could not be built or run: {}".format(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


def _gen_argv_refusal(err, name):
    """True when the stderr text err is only an unknown-option refusal by the generator name: at most one line
    starting `error:`, then a last line that is `usage: <name>` or starts with `usage: <name> `."""
    lines = err.splitlines()
    usage = "usage: " + name
    if not lines or not (lines[-1] == usage or lines[-1].startswith(usage + " ")):
        return False
    return len(lines) == 1 or (len(lines) == 2 and lines[0].startswith("error:"))


# Controls for the class check over a private tools directory: a generator that ignores its arguments and writes
# must be reported, with its write blocked; so must one that makes a FIFO, one that exits 2 for another reason
# with the usage text inside its error line, one that exits 2 with an `error:` line and a usage line whatever its
# arguments (a refusal in shape only), and one that refuses unknown options but writes under --check, with that
# write blocked; and one that refuses unknown options, writes with no argument and only checks under --check
# must not be.
_GEN_ARGV_WRITER = ("import sys\nfrom pathlib import Path\n"
                    "Path(__file__).with_name('written.txt').write_text('x')\nsys.exit(0)\n")
_GEN_ARGV_FIFO = ("import os, sys\nfrom pathlib import Path\n"
                  "os.mkfifo(str(Path(__file__).with_name('written.fifo')))\nsys.exit(0)\n")
_GEN_ARGV_OTHER2 = ("import sys\n"
                    "print('error: cannot read the source (see usage: gen_other2.py)', file=sys.stderr)\n"
                    "sys.exit(2)\n")
_GEN_ARGV_ALWAYS2 = ("import sys\n"
                     "print('error: cannot read the source; fail closed', file=sys.stderr)\n"
                     "print('usage: gen_always2.py [--check]', file=sys.stderr)\n"
                     "sys.exit(2)\n")
_GEN_ARGV_MODES = ("import sys\nfrom pathlib import Path\n"
                   "if sys.argv[1:] not in ([], ['--check']):\n"
                   "    print('usage: {name}', file=sys.stderr)\n"
                   "    sys.exit(2)\n"
                   "if {writes}:\n"
                   "    Path(__file__).with_name('written.txt').write_text('x')\n"
                   "sys.exit(0)\n")
_GEN_ARGV_REFUSER = _GEN_ARGV_MODES.format(name="gen_refuser.py", writes="not sys.argv[1:]")
_GEN_ARGV_CHECK_WRITER = _GEN_ARGV_MODES.format(name="gen_checkwriter.py", writes="True")
# Under the hook (an isolated -I child) this behaves as the refuser; in the plain form it first writes, a stand-in
# for a write the hook does not see, which only the status comparison can report.
_GEN_ARGV_PLAIN_WRITER = ("import sys\nfrom pathlib import Path\n"
                          "if not sys.flags.isolated:\n"
                          "    Path(__file__).with_name('written.txt').write_text('x')\n"
                          + _GEN_ARGV_MODES.format(name="gen_plainwriter.py", writes="not sys.argv[1:]"))
# Writes its registry target out.txt only when it differs: gen_lateguard.py before its argument guard (on a current
# tree that write never fires; on the stale tree it must be reported). The _GEN_ARGV_RECONCILE_MODES generators
# guard first, then on a difference exit 1 in check mode or write in write mode: gen_earlyguard.py runs write mode
# with no argument and check mode under --check (it must pass); gen_noargcheck.py runs check mode with no argument
# and gen_checkignorer.py runs write mode under --check (each passes on the current tree, and must be reported for
# that case on the stale tree).
_GEN_ARGV_RECONCILE = ("out = Path(__file__).resolve().parent.parent / 'out.txt'\n"
                       "if out.read_bytes() != b'generated\\n':\n"
                       "    out.write_bytes(b'generated\\n')\n")
_GEN_ARGV_GUARD = ("if sys.argv[1:] not in ([], ['--check']):\n"
                   "    print('usage: {name} [--check]', file=sys.stderr)\n"
                   "    sys.exit(2)\n")
_GEN_ARGV_LATE_GUARD = ("import sys\nfrom pathlib import Path\n" + _GEN_ARGV_RECONCILE
                        + _GEN_ARGV_GUARD.format(name="gen_lateguard.py") + "sys.exit(0)\n")
_GEN_ARGV_RECONCILE_MODES = ("import sys\nfrom pathlib import Path\n" + _GEN_ARGV_GUARD
                             + "check = {check}\n"
                             "out = Path(__file__).resolve().parent.parent / 'out.txt'\n"
                             "if out.read_bytes() != b'generated\\n':\n"
                             "    if check:\n"
                             "        sys.exit(1)\n"
                             "    out.write_bytes(b'generated\\n')\n"
                             "sys.exit(0)\n")
_GEN_ARGV_EARLY_GUARD = _GEN_ARGV_RECONCILE_MODES.format(name="gen_earlyguard.py", check="sys.argv[1:] == ['--check']")
_GEN_ARGV_NOARG_CHECK = _GEN_ARGV_RECONCILE_MODES.format(name="gen_noargcheck.py", check="True")
_GEN_ARGV_CHECK_IGNORER = _GEN_ARGV_RECONCILE_MODES.format(name="gen_checkignorer.py", check="False")
# Guards its arguments, then runs one git command behind the pins and environment of tools/gen_rules_core.py's
# merge-base read (exit 2 when git fails), then writes with no argument and only checks under --check:
# gen_basereader.py runs `rev-parse --show-toplevel` (it must pass, and be reported with the base-read allowance
# switched off), gen_refwriter.py runs `update-ref` (it must be reported, and the ref never made). The other
# variants make the same rev-parse read with one setting off its pinned value, each of which must be reported:
# GIT_NO_LAZY_FETCH set to 0, GIT_CONFIG_GLOBAL naming a file rather than the null device, GIT_NO_REPLACE_OBJECTS
# left out of the environment, and the protocol.allow=never pin left off the argv.
_GEN_ARGV_BASE_GIT = ("import os, subprocess, sys\nfrom pathlib import Path\n"
                      "if sys.argv[1:] not in ([], ['--check']):\n"
                      "    print('usage: {name}', file=sys.stderr)\n"
                      "    sys.exit(2)\n"
                      "env = {{key: value for key, value in os.environ.items() if not key.startswith('GIT_')}}\n"
                      "env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_LAZY_FETCH='1',\n"
                      "           GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1',\n"
                      "           GIT_GRAFT_FILE=os.devnull)\n"
                      "{tweak}\n"
                      "done = subprocess.run(['git', '-C', os.getcwd(), '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',\n"
                      "                       '-c', 'maintenance.auto=false', '-c', 'core.commitGraph=false',\n"
                      "                       {protocol}{sub}],\n"
                      "                      capture_output=True, env=env)\n"
                      "if done.returncode != 0:\n"
                      "    sys.exit(2)\n"
                      "if not sys.argv[1:]:\n"
                      "    Path(__file__).with_name('written.txt').write_text('x')\n"
                      "sys.exit(0)\n")
_GEN_ARGV_PROTOCOL_PIN = "'-c', 'protocol.allow=never', "
_GEN_ARGV_SHOW_TOPLEVEL = "'rev-parse', '--show-toplevel'"
_GEN_ARGV_BASE_READER = _GEN_ARGV_BASE_GIT.format(name="gen_basereader.py", tweak="pass",
                                                  protocol=_GEN_ARGV_PROTOCOL_PIN, sub=_GEN_ARGV_SHOW_TOPLEVEL)
_GEN_ARGV_REF_WRITER = _GEN_ARGV_BASE_GIT.format(name="gen_refwriter.py", tweak="pass",
                                                 protocol=_GEN_ARGV_PROTOCOL_PIN,
                                                 sub="'update-ref', 'refs/heads/aiqt-control', 'HEAD'")
# (name, the line that sets one value off its pin, the protocol pin as written)
_GEN_ARGV_BASE_OFF_PIN = (
    ("gen_lazyfetch.py", "env['GIT_NO_LAZY_FETCH'] = '0'", _GEN_ARGV_PROTOCOL_PIN),
    ("gen_globalcfg.py", "env['GIT_CONFIG_GLOBAL'] = os.path.join(os.getcwd(), 'absent-global.cfg')",
     _GEN_ARGV_PROTOCOL_PIN),
    ("gen_replaceobj.py", "del env['GIT_NO_REPLACE_OBJECTS']", _GEN_ARGV_PROTOCOL_PIN),
    ("gen_noprotocol.py", "pass", ""))


def _gen_argv_base_read_failures(tmp):
    """The controls for the class check's allowance of tools/gen_rules_core.py's merge-base read, each in a git
    repository under tmp: one failure line per control that does not discriminate. A generator making that read
    (rev-parse behind its pins and environment) must pass; with the base-read allowance switched off (the flip) it
    must be reported for exactly its no-argument and --check cases, the git launch blocked; one running update-ref
    behind the same pins must be reported so too, and its ref never made. Each _GEN_ARGV_BASE_OFF_PIN variant (one
    git variable off its pinned value or left out, or the protocol.allow=never pin left off) must be reported the
    same way, and must pass with the matching check switched off in the child (the flips: the variable values
    then judged by name only, or the protocol pin not required), so each check is shown to carry weight."""
    failures = []
    saved_child = _GEN_ARGV_CHILD
    values_off = ("_BASE_ENV_PINNED = True", "_BASE_ENV_PINNED = False")
    protocol_off = ("_BASE_PROTOCOL_PINNED = True", "_BASE_PROTOCOL_PINNED = False")
    controls = [("gen_basereader.py", _GEN_ARGV_BASE_READER, None, False),
                ("gen_basereader.py", _GEN_ARGV_BASE_READER, ("_BASE_READS_ON = True", "_BASE_READS_ON = False"), True),
                ("gen_refwriter.py", _GEN_ARGV_REF_WRITER, None, True)]
    for name, tweak, protocol in _GEN_ARGV_BASE_OFF_PIN:
        source = _GEN_ARGV_BASE_GIT.format(name=name, tweak=tweak, protocol=protocol, sub=_GEN_ARGV_SHOW_TOPLEVEL)
        controls.append((name, source, None, True))
        controls.append((name, source, protocol_off if not protocol else values_off, False))
    for number, (name, source, switch, want) in enumerate(controls):
        label = "on" if switch is None else "with {}".format(switch[1])
        repo = tmp / "repo-base-{}-{}".format(number, name[:-3])
        (repo / "tools").mkdir(parents=True)
        (repo / "tools" / name).write_text(source, encoding="utf-8")
        problem = _gen_argv_commit(repo)
        if switch is not None and switch[0] not in saved_child:
            problem = problem or "the child holds no {!r} to switch".format(switch[0])
        if switch is not None:
            globals()["_GEN_ARGV_CHILD"] = saved_child.replace(switch[0], switch[1])
        try:
            got = [problem] if problem else _gen_argv_class_failures(repo / "tools", repo)
        finally:
            globals()["_GEN_ARGV_CHILD"] = saved_child
        launch_blocked = [line for line in got if "subprocess.Popen" in line
                          and (line.startswith(name + " (no argument): ") or line.startswith(name + " --check: "))]
        made = _gen_argv_git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/aiqt-control").returncode == 0
        if problem or made or ((len(launch_blocked) != 2 or len(got) != 2) if want else got):
            failures.append("class-check control: in a git repository, {} with the base-read allowance {} must {}; "
                            "got {}, refs/heads/aiqt-control {}".format(
                                name, label, "be reported for its no-argument and --check cases, the git launch "
                                "blocked" if want else "pass", got, "made" if made else "absent"))
    return failures


def _gen_argv_class_check_failures():
    """The class check over the real tools/ directory, after its controls discriminate."""
    import shutil
    import tempfile
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-argv-selftest-"))
    try:
        (tmp / "writer").mkdir()
        (tmp / "writer" / "gen_writer.py").write_text(_GEN_ARGV_WRITER, encoding="utf-8")
        (tmp / "refuser").mkdir()
        (tmp / "refuser" / "gen_refuser.py").write_text(_GEN_ARGV_REFUSER, encoding="utf-8")
        writer = _gen_argv_class_failures(tmp / "writer")
        if not writer or (tmp / "writer" / "written.txt").exists():
            failures.append("class-check control: a generator that ignores its arguments and writes must be "
                            "reported with its write blocked; got {} failure line(s), written.txt {}".format(
                                len(writer), "present" if (tmp / "writer" / "written.txt").exists() else "absent"))
        (tmp / "fifo").mkdir()
        (tmp / "fifo" / "gen_fifo.py").write_text(_GEN_ARGV_FIFO, encoding="utf-8")
        fifo = _gen_argv_class_failures(tmp / "fifo")
        if not fifo or os.path.lexists(tmp / "fifo" / "written.fifo"):
            failures.append("class-check control: a generator that ignores its arguments and makes a FIFO must be "
                            "reported with the FIFO blocked; got {} failure line(s), written.fifo {}".format(
                                len(fifo), "present" if os.path.lexists(tmp / "fifo" / "written.fifo") else "absent"))
        (tmp / "other2").mkdir()
        (tmp / "other2" / "gen_other2.py").write_text(_GEN_ARGV_OTHER2, encoding="utf-8")
        if not _gen_argv_class_failures(tmp / "other2"):
            failures.append("class-check control: a generator that exits 2 for another reason, with the usage text "
                            "inside its error line, must be reported; it passed")
        (tmp / "always2").mkdir()
        (tmp / "always2" / "gen_always2.py").write_text(_GEN_ARGV_ALWAYS2, encoding="utf-8")
        always2 = _gen_argv_class_failures(tmp / "always2")
        if not any("(no argument)" in line for line in always2) or not any(" --check: " in line for line in always2):
            failures.append("class-check control: a generator that exits 2 with an error: line and a usage line "
                            "whatever its arguments must be reported for no argument and for --check; got "
                            "{}".format(always2))
        (tmp / "checkwriter").mkdir()
        (tmp / "checkwriter" / "gen_checkwriter.py").write_text(_GEN_ARGV_CHECK_WRITER, encoding="utf-8")
        checkwriter = _gen_argv_class_failures(tmp / "checkwriter")
        if (not any(" --check: " in line for line in checkwriter)
                or (tmp / "checkwriter" / "written.txt").exists()):
            failures.append("class-check control: a generator that writes under --check must be reported with its "
                            "write blocked; got {}, written.txt {}".format(checkwriter, "present" if (
                                tmp / "checkwriter" / "written.txt").exists() else "absent"))
        refuser = _gen_argv_class_failures(tmp / "refuser")
        if refuser or (tmp / "refuser" / "written.txt").exists():
            failures.append("class-check control: a generator that refuses unknown options, writes with no argument "
                            "and only checks under --check must pass with its write blocked; got {}, written.txt "
                            "{}".format(refuser, "present" if (tmp / "refuser" / "written.txt").exists() else "absent"))
        # In a throwaway git repository: the same refuser must pass the plain form and the status comparison, and
        # one that behaves so under the hook but writes in the plain form (a write the hook would not see) must be
        # reported for the change.
        for name, source, want in (("gen_refuser.py", _GEN_ARGV_REFUSER, False),
                                   ("gen_plainwriter.py", _GEN_ARGV_PLAIN_WRITER, True)):
            repo = tmp / ("repo-" + name[:-3])
            (repo / "tools").mkdir(parents=True)
            (repo / "tools" / name).write_text(source, encoding="utf-8")
            problem = _gen_argv_commit(repo)
            got = [problem] if problem else _gen_argv_class_failures(repo / "tools", repo)
            reported = any(" changed the copy " in line for line in got)
            if problem or ((len(got) != 1 or not reported) if want else got):
                failures.append("class-check control: in a git repository, {} must {}; got {}".format(
                    name, "be reported only for its plain-form write" if want else "pass", got))
        failures.extend(_gen_argv_base_read_failures(tmp))
        # In a git repository whose registry declares out.txt, current at the commit: a generator that writes it on
        # a difference before its argument guard must be reported for the refusal cases (the write blocked) and for
        # the plain form (the stale file rewritten), with out.txt back as committed; one that does so only after
        # its guard, in write mode with no argument and check mode under --check, must pass; one that runs check
        # mode with no argument must be reported for exactly the no-argument case on the stale tree, and one whose
        # --check runs write mode for exactly the --check case on the stale tree.
        for name, source, want in (("gen_lateguard.py", _GEN_ARGV_LATE_GUARD, True),
                                   ("gen_earlyguard.py", _GEN_ARGV_EARLY_GUARD, False),
                                   ("gen_noargcheck.py", _GEN_ARGV_NOARG_CHECK, " (no argument) on the stale tree: "),
                                   ("gen_checkignorer.py", _GEN_ARGV_CHECK_IGNORER, " --check on the stale tree: ")):
            repo = tmp / ("repo-" + name[:-3])
            (repo / "tools").mkdir(parents=True)
            (repo / ".aiqt").mkdir()
            (repo / "tools" / name).write_text(source, encoding="utf-8")
            (repo / "out.txt").write_bytes(b"generated\n")
            (repo / _GEN_ARGV_REGISTRY).write_text(
                '{"generated": [{"kind": "file", "regenerate": "python3 tools/' + name + '", "sources": ["tools/"], '
                '"target": "out.txt"}], "version": 1}\n', encoding="utf-8")
            problem = _gen_argv_commit(repo)
            got = [problem] if problem else _gen_argv_class_failures(repo / "tools", repo)
            blocked = any(line.startswith(name + " --chek: ") and "blocked: open" in line for line in got)
            rewrote = any(line.startswith(name + " --chek (plain form): the run changed the generated file "
                                          "out.txt") for line in got)
            intact = (repo / "out.txt").read_bytes() == b"generated\n"
            if want is True:
                wrong, expected = not blocked or not rewrote, ("be reported for its write before the argument guard, "
                                                               "blocked in the refusal cases and seen in the plain form")
            elif want:
                wrong = len(got) != 1 or not got[0].startswith(name + want)
                expected = "be reported only by the line starting {!r}".format(name + want)
            else:
                wrong, expected = bool(got), "pass"
            if problem or not intact or wrong:
                failures.append("class-check control: on a stale tree, {} must {}; got {}, out.txt {}".format(
                    name, expected, got, "as committed" if intact else "changed"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # The real generators run only in a throwaway copy of the committed tree, never in the working tree.
    copy_tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-argv-copy-"))
    try:
        copy = copy_tmp / "tree"
        problem = _gen_argv_head_copy(Path(__file__).resolve().parent.parent, copy)
        if problem:
            failures.append(problem)
        else:
            tools = copy / "tools"
            if not (tools / "gen_rules.py").is_file():
                failures.append("class check: tools/gen_rules.py is not in the enumerated directory {}".format(tools))
            if not (copy / _GEN_ARGV_REGISTRY).is_file():
                failures.append("class check: the copy holds no {}, so no refusal case ran on a stale tree".format(
                    _GEN_ARGV_REGISTRY))
            failures.extend(_gen_argv_class_failures(tools, copy))
    finally:
        shutil.rmtree(copy_tmp, ignore_errors=True)
    return failures


# One focused invariant (the sibling generators' idiom): an invalid-UTF-8 GENERATED TARGET fails closed
# (exit 2) rather than a raw UnicodeDecodeError traceback. The reconcile loop reads each desired target as
# UTF-8 (the drift compare), so a non-UTF-8 target must be caught by the widened (OSError, UnicodeError)
# arm, so an unreadable target fails closed instead of crashing. A revert to the narrow OSError-only arm makes run() RAISE instead of returning 2, so this
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

# The second invariant: the import-time MAP_KEYS refusal keeps exit 2 and an empty stdout whatever
# sys.stderr does. A child imports this module with map_keys raising OSError, under a piped stderr
# (the diagnostic must arrive), a None stderr, a stderr whose write raises and one whose flush raises.
# The retired print/`raise SystemExit(2)` shape fails three of the four: print to a None stream writes
# the diagnostic to stdout, and a stream whose write or flush raises turns the exit into 120.
_MAP_KEYS_CHILD = """import sys
sys.path.insert(0, sys.argv[1])
import _standards
def _refuse(root):
    raise PermissionError(13, "self-test: unlistable standards directory")
_standards.map_keys = _refuse
class _Stream:
    def write(self, text):
        if sys.argv[2] == "write-raises":
            raise OSError(28, "self-test: write failed")
        return len(text)
    def flush(self):
        if sys.argv[2] in ("write-raises", "flush-raises"):
            raise OSError(28, "self-test: flush failed")
if sys.argv[2] != "piped":
    sys.stderr = None if sys.argv[2] == "none" else _Stream()
import gen_rules
"""
_MAP_KEYS_CONDITIONS = ("piped", "none", "write-raises", "flush-raises")


def _map_keys_refusal_failures():
    """One failure line per stderr condition under which the MAP_KEYS refusal does not exit 2 with
    empty stdout (and, with stderr piped, the diagnostic on stderr)."""
    import subprocess
    tools = str(Path(__file__).resolve().parent)
    failures = []
    for condition in _MAP_KEYS_CONDITIONS:
        try:
            done = subprocess.run([sys.executable, "-I", "-B", "-c", _MAP_KEYS_CHILD, tools, condition],
                                  capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append("MAP_KEYS refusal ({}): the child did not run: {}".format(condition, exc))
            continue
        got = (done.returncode, done.stdout, condition != "piped" or b"(fail-closed)" in done.stderr)
        if got != (2, b"", True):
            failures.append("MAP_KEYS refusal with stderr {}: expected exit 2, empty stdout and (piped) "
                            "the diagnostic; got exit {}, stdout {!r}, stderr {!r}".format(
                                condition, done.returncode, done.stdout[:200], done.stderr[-300:]))
    return failures

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
# Red on revert for the PyYAML value comparison, which ADOPTER MODE and the strict read share: the read
# made to alter one value and keep every key (the slug gets a suffix), in each mode alone.
# (fixed text, reverted text)
_FM_VALUE = '_value(key, val.strip(" "))'
_ADOPTER_ALTER_VALUE = ("fm[key] = " + _FM_VALUE,
                        "fm[key] = {} + '-x' if adopter and key == 'slug' else {}".format(_FM_VALUE, _FM_VALUE))
_STRICT_ALTER_VALUE = ("fm[key] = " + _FM_VALUE,
                       "fm[key] = {} + '-x' if not adopter and key == 'slug' else {}".format(_FM_VALUE, _FM_VALUE))


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
    # Setext underlines with trailing space or tab, quoted and reopened HTML, wide list markers and
    # zero-width characters around Detail: each exits 2.
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
    # Balanced links, comments and multi-line HTML around Detail, filler and homoglyph letters, and a
    # link reference definition in the detail layer: each exits 2.
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
    # A title with parentheses and no bracket is plain, with or without the split (the title is set by
    # _CASE_FRAME). Under the BODY GRAMMAR no other heading is allowed.
    ("r5-paren-heading", "", "", 0),
    ("r5-paren-heading-split", _TRIGGER, _DETAIL_BODY, 0),
    ("r5-paren-subheading", _TRIGGER, "\n## Scope (core)\n" + _DETAIL_BODY + "\n### Notes (a)(b) ()\n", 2),
    # Under the BODY GRAMMAR a sub-heading and a list are refused, and the prose shapes the live corpus
    # uses are admitted (exit 0).
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
    # A fenced comment opener, an ordered item above a setext underline, and a heading or comment line in
    # the frontmatter: each exits 2.
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
    # A frontmatter value holds no `<`, `[`, `]` or backslash, so no HTML or link in it adds a
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
    # The hidden-character check reads the raw frontmatter line before any strip, so a
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
    # Each key has a declared value type, and only its shapes are accepted: the shapes outside it exit 2
    # and the declared shapes exit 0.
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
    # A value YAML reads as another type is refused (a date or integer slug, a float-shaped
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
    ("r10-duplicate-field", _TRIGGER + "detail-trigger: writing another fixture\n", _DETAIL_BODY, 2),
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
# The FRONTMATTER grammar guards, each a (fixed text, reverted text) pair.
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
    # refuses every one of these Markdown shapes.
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
    _revert("fm-r7b-ascii-int", "r7b-frontmatter-non-ascii-tier", (_FMTIER[0], "_TIER_RE = re.compile(r'\\d\\d')"),
            ('if v.removeprefix("-").isdecimal() and not v.isascii():', "if False:")),
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
    _revert("fm-r10-duplicate-field", "r10-duplicate-field", _FMDUP),
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
# The split is invisible in every generated output: a split source (the rule's current text moved below
# `## Detail` unchanged, a core layer added between its title and the split, and the detail keys added to its
# frontmatter) must render byte-equal to its unsplit original, in write mode and in the --check drift compare
# (a target holding the unsplit original is in sync, and one holding the split source as written is drift).
# Each original is also a source of its own, which renders as itself. core_layer() must return the core text
# of the split source and None for the original. (name, family lines, detail key lines placed (before
# corpus-id, before slug, after slug), body lines above the title, core section between the title and the
# split, the current text below the title, the expected core_layer)
_SPLIT_FM = ("---\n{head}corpus-id: selfs1\norigin: pack\n{family}{mid}slug: gen-rules-selftest-split\n{tail}"
             "---\n")
_SPLIT_NAME = "gen-rules-selftest-split.md"
_SPLIT_AIQT = "family: aiqt\ntier: 10\nfacet: QUALI\n"
_SPLIT_TITLE = "# Gen-rules split self-test rule\n"
_SPLIT_TEXT = ("\nNever blend a measured figure with an estimated one; label which part is which.\n"
               "Report an unknown as unknown, never as zero.\n\nA second paragraph, kept verbatim (it is the "
               "rule's current text).\n")
_SPLIT_CORE = "\nNever blend measured and estimated figures.\n\n"
_SPLIT_CASES = (
    ("split-aiqt-both-keys", _SPLIT_AIQT, ("", "", _TRIGGER + _REASON), "", _SPLIT_CORE, _SPLIT_TEXT,
     "Never blend measured and estimated figures.\n"),
    ("split-security-trigger-mid", "family: security\nfacet: SECI\n", ("", _TRIGGER, ""), "",
     "\nCore one.\n\nCore two,\nstill core two.\n\n", _SPLIT_TEXT, "Core one.\n\nCore two,\nstill core two.\n"),
    ("split-keys-first", _SPLIT_AIQT, (_REASON + _TRIGGER, "", ""), "", _SPLIT_CORE, _SPLIT_TEXT,
     "Never blend measured and estimated figures.\n"),
    ("split-quoted-spaced-keys", _SPLIT_AIQT,
     ("", "", "detail-trigger: 'writing a self-test fixture'\ndetail-reason:   self-test only  \n"), "",
     _SPLIT_CORE, _SPLIT_TEXT, "Never blend measured and estimated figures.\n"),
    ("split-non-ascii-reason", _SPLIT_AIQT, ("", "", _TRIGGER + "detail-reason: caf" + chr(0xe9) + " review\n"),
     "", _SPLIT_CORE, _SPLIT_TEXT, "Never blend measured and estimated figures.\n"),
    ("split-blank-before-title", _SPLIT_AIQT, ("", "", _TRIGGER), "\n", _SPLIT_CORE, _SPLIT_TEXT,
     "Never blend measured and estimated figures.\n"),
    ("split-text-at-title", _SPLIT_AIQT, ("", "", _TRIGGER), "", _SPLIT_CORE,
     "Text directly under the title.\n\nA second paragraph.\n", "Never blend measured and estimated figures.\n"),
    ("split-wide-gaps", _SPLIT_AIQT, ("", "", _TRIGGER), "", "\n\nCore.\n\n\n", "\n\n" + _SPLIT_TEXT, "Core.\n"),
)


def _unsplit_reference(text):
    """A second implementation of full_body for a split source that the corpus accepts, by text patterns
    rather than line numbers: drop each frontmatter line that starts with a detail key and `:`, then drop
    the text from the end of the title line through the end of the `## Detail` line."""
    head, sep, body = text.partition("\n---\n")
    head = "\n".join(line for line in head.split("\n")
                     if not re.match(r"(?:detail-trigger|detail-reason) *:", line))
    body = re.sub(r"\A(\n*# [^\n]*\n).*?^## Detail\n", r"\1", body, count=1, flags=re.S | re.M)
    return head + sep + body


def _split_case_texts(case):
    """(unsplit original, split source) of one _SPLIT_CASES case."""
    _name, family, (head, mid, tail), lead, core, text, _core = case
    original = _SPLIT_FM.format(head="", mid="", tail="", family=family) + lead + _SPLIT_TITLE + text
    split = (_SPLIT_FM.format(head=head, mid=mid, tail=tail, family=family) + lead + _SPLIT_TITLE + core
             + DETAIL_HEADING + "\n" + text)
    return original, split


def _split_render_failures(module, base, run_quiet):
    """One failure line per way module (this module, or one with a guard reverted) fails a _SPLIT_CASES case:
    the split source written in write mode must give exit 0 and the unsplit original byte for byte, then
    --check exit 0; --check over a target holding the original must exit 0 and over one holding the split
    source must exit 1 (drift); the original as a source must render as itself; full_body() must give the
    original for both texts and core_layer() the expected core for the split source and None for the
    original. base is a directory this call may populate."""
    failures = []
    full, core = getattr(module, "full_body", None), getattr(module, "core_layer", None)
    for case in _SPLIT_CASES:
        name, expected_core = case[0], case[-1]
        original, split = _split_case_texts(case)
        rel = derive(parse_source_text(original, name), name)
        for mode, source, prewrite, want in (("write", split, None, 0), ("control", original, None, 0),
                                             ("check-original", split, original, 0),
                                             ("check-leaked", split, split, 1)):
            root = base / name / mode
            (root / ".aiqt" / "core" / "rules").mkdir(parents=True)
            (root / ".aiqt" / "core" / "rules" / _SPLIT_NAME).write_bytes(source.encode("utf-8"))
            target = root / ".claude" / "rules" / rel
            if prewrite is not None:
                # The same bytes in the read tree and in the reference tree, so each check case judges
                # the one render in both.
                for held_at in (target, root / REFERENCE_DIR / rel):
                    held_at.parent.mkdir(parents=True)
                    held_at.write_bytes(prewrite.encode("utf-8"))
            got = run_quiet(root, prewrite is not None, runner=module.run)
            if got != want:
                failures.append("split case {} ({}): expected exit {}, got {!r}".format(name, mode, want, got))
            elif prewrite is None:
                held = target.read_bytes() if target.is_file() else None
                if held != original.encode("utf-8"):
                    failures.append("split case {} ({}): the generated file is not the unsplit original byte for "
                                    "byte; got {!r}".format(name, mode, None if held is None else held[-300:]))
                reference = root / REFERENCE_DIR / rel
                ref_held = reference.read_bytes() if reference.is_file() else None
                if ref_held != original.encode("utf-8"):
                    failures.append("split case {} ({}): the reference file is not the unsplit original byte for "
                                    "byte; got {!r}".format(name, mode,
                                                            None if ref_held is None else ref_held[-300:]))
                again = run_quiet(root, True, runner=module.run)
                if again != 0:
                    failures.append("split case {} ({}): --check after generation expected exit 0, got {!r}"
                                    .format(name, mode, again))
        if full is None or core is None:
            failures.append("split case {}: full_body() and core_layer() must both be defined".format(name))
            continue
        try:
            got = (full(split, name), full(original, name), core(split, name), core(original, name))
        except Exception as exc:  # noqa: BLE001  a revert may surface as any exception
            got = "raised {!r}".format(exc)
        if got != (original, original, expected_core, None):
            failures.append("split case {}: expected (full_body of the split source, full_body of the original, "
                            "core_layer of the split source, core_layer of the original) to be (the original, the "
                            "original, {!r}, None); got {!r}".format(name, expected_core, got))
    return failures


# Red on revert for the split render: each guard put back makes at least one _SPLIT_CASES case fail.
# (name, fixed text, reverted text)
_SPLIT_REVERTS = (
    ("split-render-wiring", "desired[rel] = full_body(read_rule_source(src), src.name)",
     "desired[rel] = read_rule_source(src)"),
    ("split-drop-detail-keys", " if _frontmatter_key(line) not in DETAIL_KEYS]", "]"),
    ("split-drop-core", "lines[first - 2:title] + lines[split:]", "lines[first - 2:split - 1] + lines[split:]"),
    ("split-drop-heading", "lines[first - 2:title] + lines[split:]", "lines[first - 2:title] + lines[split - 1:]"),
    ("split-core-below-title", "core = lines[title:split - 1]", "core = lines[title - 1:split - 1]"),
)


# The reference tree (REFERENCE TREE above). A fixture corpus of an unsplit aiqt rule (_RULE_SRC) and a split
# security rule (the second _SPLIT_CASES case) is generated, then each drift case alters the generated reference
# tree and must be exit 1 in --check with every byte of the fixture tree unchanged, then exit 0 in write mode
# and exit 0 in --check again (the write repairs it). (name, alteration of the case root)
_REFERENCE_SPLIT = _split_case_texts(_SPLIT_CASES[1])
_REFERENCE_SPLIT_REL = "security/SECI-gen-rules-selftest-split.md"


def _ref_missing(root):
    (root / REFERENCE_DIR / _RULE_REL).unlink()


def _ref_edited(root):
    path = root / REFERENCE_DIR / _REFERENCE_SPLIT_REL
    path.write_bytes(path.read_bytes() + b"An edited line.\n")


def _ref_extra_md(root):
    (root / REFERENCE_DIR / "aiqt" / "zz-extra-rule.md").write_bytes(b"# Extra\n")


def _ref_extra_other(root):
    (root / REFERENCE_DIR / "security" / "notes.txt").write_bytes(b"not a rule\n")


def _ref_crlf(root):
    path = root / REFERENCE_DIR / _RULE_REL
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


def _ref_lone_cr(root):
    path = root / REFERENCE_DIR / _REFERENCE_SPLIT_REL
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r", 1))


def _ref_not_utf8(root):
    (root / REFERENCE_DIR / _RULE_REL).write_bytes(b"\xff\xfe not utf-8\n")


def _ref_tree_gone(root):
    import shutil
    shutil.rmtree(root / ".aiqt" / "reference")


def _ref_empty_subdir(root):
    (root / REFERENCE_DIR / "aiqt" / "sub").mkdir()


def _ref_nested_orphan(root):
    (root / REFERENCE_DIR / "security" / "deep" / "deeper").mkdir(parents=True)
    (root / REFERENCE_DIR / "security" / "deep" / "deeper" / "old.md").write_bytes(b"# Old\n")


_REFERENCE_DRIFT_CASES = (("missing", _ref_missing), ("edited", _ref_edited), ("extra-md", _ref_extra_md),
                          ("extra-other", _ref_extra_other), ("crlf", _ref_crlf), ("lone-cr", _ref_lone_cr),
                          ("not-utf8", _ref_not_utf8), ("tree-gone", _ref_tree_gone),
                          ("empty-subdir", _ref_empty_subdir), ("nested-orphan", _ref_nested_orphan))


# The reference tree is confined to its two declared output trees and never followed through a link: each case
# alters a generated fixture and must be exit 2 in --check and in write mode with every byte of the fixture tree
# (its .aiqt/core/rules/ sources included) and of the outside directory unchanged. (name, alteration of
# (root, outside))
def _ref_family_link_in_root(root, _outside):
    import shutil
    shutil.rmtree(root / REFERENCE_DIR / "security")
    (root / REFERENCE_DIR / "security").symlink_to(Path("..", "..", "core", "rules"))


def _ref_rules_link_in_root(root, _outside):
    import shutil
    shutil.rmtree(root / REFERENCE_DIR)
    (root / REFERENCE_DIR).symlink_to(Path("..", "core", "rules"))


def _ref_subdir_link_in_root(root, _outside):
    (root / REFERENCE_DIR / "aiqt" / "sub").symlink_to(Path("..", "..", "..", "core", "rules"))


def _ref_orphan_symlink(root, outside):
    (outside / "held.md").write_bytes(b"held outside\n")
    (root / REFERENCE_DIR / "aiqt" / "zz-link.md").symlink_to(outside / "held.md")


def _ref_stray_file(root, _outside):
    (root / REFERENCE_DIR / "stray.md").write_bytes(b"# Stray\n")


def _ref_stray_dir(root, _outside):
    (root / REFERENCE_DIR / "extra").mkdir()
    (root / REFERENCE_DIR / "extra" / "x.md").write_bytes(b"# Extra\n")


def _ref_sibling_file(root, _outside):
    (root / ".aiqt" / "reference" / "other.md").write_bytes(b"# Other\n")


# The read tree (RULES_DIR) gets the same refusals: each was followed, read or pruned through before the walk.
def _rules_family_link_in_root(root, _outside):
    import shutil
    shutil.rmtree(root / RULES_DIR / "security")
    (root / RULES_DIR / "security").symlink_to(Path("..", "..", ".aiqt", "core", "rules"))


def _rules_dir_link_in_root(root, _outside):
    import shutil
    shutil.rmtree(root / RULES_DIR)
    (root / RULES_DIR).symlink_to(Path("..", ".aiqt", "core"))


def _rules_subdir_link_in_root(root, _outside):
    (root / RULES_DIR / "aiqt" / "sub").symlink_to(Path("..", "..", "..", ".aiqt", "core", "rules"))


def _rules_target_symlink(root, outside):
    target = root / RULES_DIR / _RULE_REL
    target.unlink()
    (outside / "held.md").write_bytes(b"held outside\n")
    target.symlink_to(outside / "held.md")


def _rules_orphan_symlink(root, outside):
    (outside / "held.md").write_bytes(b"held outside\n")
    (root / RULES_DIR / "aiqt" / "zz-link.md").symlink_to(outside / "held.md")


def _rules_fifo(root, _outside):
    os.mkfifo(root / RULES_DIR / "security" / "zz-fifo.md")


_REFERENCE_REFUSED_CASES = (("family-link-in-root", _ref_family_link_in_root),
                            ("rules-link-in-root", _ref_rules_link_in_root),
                            ("subdir-link-in-root", _ref_subdir_link_in_root),
                            ("orphan-symlink", _ref_orphan_symlink), ("stray-file", _ref_stray_file),
                            ("stray-dir", _ref_stray_dir), ("sibling-file", _ref_sibling_file),
                            ("rules-family-link-in-root", _rules_family_link_in_root),
                            ("rules-dir-link-in-root", _rules_dir_link_in_root),
                            ("rules-subdir-link-in-root", _rules_subdir_link_in_root),
                            ("rules-target-symlink", _rules_target_symlink),
                            ("rules-orphan-symlink", _rules_orphan_symlink)) + (
                            (("rules-fifo", _rules_fifo),) if hasattr(os, "mkfifo") else ())


def _tree_bytes(root):
    """Every entry under root, followed by no link: {relative path: bytes for a file, ('link', target) for a
    symbolic link, 'dir' for a directory, ('special', file type) for a FIFO, socket or device (never opened)}."""
    held = {}
    for dirpath, dirs, filenames in os.walk(root):
        for name in dirs + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                held[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                held[rel] = "dir"
            elif not path.is_file():
                held[rel] = ("special", stat.S_IFMT(path.lstat().st_mode))
            else:
                held[rel] = path.read_bytes()
    return held


def _reference_root(base, name):
    """A fixture corpus at base/name holding the unsplit aiqt rule and the split security rule; returns it."""
    root = base / name
    src = root / ".aiqt" / "core" / "rules"
    src.mkdir(parents=True)
    (src / "gen-rules-selftest-target.md").write_bytes(_RULE_SRC.encode("utf-8"))
    (src / _SPLIT_NAME).write_bytes(_REFERENCE_SPLIT[1].encode("utf-8"))
    return root


def _reference_failures(module, base, run_quiet):
    """One failure line per way module (this module, or one with a guard reverted) fails a reference-tree case.
    base is a directory this call may populate."""
    failures = []
    expected = {_RULE_REL: _RULE_SRC.encode("utf-8"), _REFERENCE_SPLIT_REL: _REFERENCE_SPLIT[0].encode("utf-8")}

    def ref_files(root):
        ref = root / REFERENCE_DIR
        return {path.relative_to(ref).as_posix(): path.read_bytes()
                for path in sorted(ref.rglob("*")) if path.is_file()} if ref.is_dir() else None

    def not_generated(root):
        # None when a write-mode run generates the fixture (exit 0, the reference tree as expected), else the
        # failure text, carrying the exit so an exception from module shows as run_quiet's 'raised ...'.
        got = run_quiet(root, False, runner=module.run)
        if got == 0 and ref_files(root) == expected:
            return None
        return "the fixture did not generate (write exit {!r})".format(got)

    # Generated: each reference file is its source's full render (the split source as its unsplit original),
    # equal to the read tree's file, and nothing else is there; --check is then clean.
    root = _reference_root(base, "generated")
    got = (run_quiet(root, False, runner=module.run), run_quiet(root, True, runner=module.run))
    claude = {rel: (root / ".claude" / "rules" / rel).read_bytes() if (root / ".claude" / "rules" / rel).is_file()
              else None for rel in expected}
    if got != (0, 0) or ref_files(root) != expected or claude != expected:
        failures.append("reference generated: expected write and --check exit 0, the reference tree holding each "
                        "source's full render and the read tree the same bytes; got exits {!r}, reference {!r}"
                        .format(got, ref_files(root)))
    for name, alter in _REFERENCE_DRIFT_CASES:
        root = _reference_root(base, "drift-" + name)
        problem = not_generated(root)
        if problem is not None:
            failures.append("reference {}: {}".format(name, problem))
            continue
        alter(root)
        before = _tree_bytes(root)
        checked = run_quiet(root, True, runner=module.run)
        unchanged = _tree_bytes(root) == before
        written = run_quiet(root, False, runner=module.run)
        again = run_quiet(root, True, runner=module.run)
        if (checked, unchanged, written, again, ref_files(root)) != (1, True, 0, 0, expected):
            failures.append("reference {}: expected --check exit 1 with nothing written, then write exit 0 and "
                            "--check exit 0 with the reference tree restored; got --check {!r}, tree unchanged {}, "
                            "write {!r}, --check {!r}".format(name, checked, unchanged, written, again))
    # A malformed source, a reference target that is a symbolic link, and a reference directory linked outside
    # the root: exit 2 in both modes with every byte of the fixture tree (and the outside directory) unchanged.
    for name in ("malformed-source", "target-symlink", "reference-dir-outside"):
        root = _reference_root(base, "refused-" + name)
        outside = base / ("outside-" + name)
        outside.mkdir()
        problem = None if name == "reference-dir-outside" else not_generated(root)
        if problem is not None:
            failures.append("reference {}: {}".format(name, problem))
            continue
        if name == "malformed-source":
            (root / ".aiqt" / "core" / "rules" / "gen-rules-selftest-target.md").write_bytes(
                _RULE_SRC.replace("tier: 10", "tier: 11").encode("utf-8"))
        elif name == "target-symlink":
            target = root / REFERENCE_DIR / _RULE_REL
            target.unlink()
            (outside / "held.md").write_bytes(b"held outside\n")
            target.symlink_to(outside / "held.md")
        else:
            (root / ".aiqt" / "reference").symlink_to(outside)
        before, outside_before = _tree_bytes(root), _tree_bytes(outside)
        got = (run_quiet(root, True, runner=module.run), run_quiet(root, False, runner=module.run))
        if got != (2, 2) or _tree_bytes(root) != before or _tree_bytes(outside) != outside_before:
            failures.append("reference {}: expected exit 2 in --check and in write mode with nothing written; got "
                            "{!r}, tree unchanged {}, outside unchanged {}".format(
                                name, got, _tree_bytes(root) == before, _tree_bytes(outside) == outside_before))
    # A symlink or an entry outside the two declared reference trees: exit 2 in both modes, nothing followed,
    # written or deleted, the sources included.
    for name, alter in _REFERENCE_REFUSED_CASES:
        root = _reference_root(base, "refused-" + name)
        outside = base / ("outside-" + name)
        outside.mkdir()
        problem = not_generated(root)
        if problem is not None:
            failures.append("reference {}: {}".format(name, problem))
            continue
        alter(root, outside)
        before, outside_before = _tree_bytes(root), _tree_bytes(outside)
        got = (run_quiet(root, True, runner=module.run), run_quiet(root, False, runner=module.run))
        if got != (2, 2) or _tree_bytes(root) != before or _tree_bytes(outside) != outside_before:
            failures.append("reference {}: expected exit 2 in --check and in write mode with nothing written or "
                            "deleted; got {!r}, tree unchanged {}, outside unchanged {}".format(
                                name, got, _tree_bytes(root) == before, _tree_bytes(outside) == outside_before))
    # The aiqt family swapped for a symlink to an outside directory after the scan judged the tree and before
    # anything is read, written or pruned (a stand-in for module._reference_scan does the swap): the reads, the
    # writes and the pruning are bound beneath descriptors, so both modes are exit 2 with the outside directory
    # unchanged, never read, written or pruned through the link.
    for mode, check in (("check", True), ("write", False)):
        name = "swap-after-scan-" + mode
        root = _reference_root(base, name)
        outside = base / ("outside-" + name)
        outside.mkdir()
        problem = not_generated(root)
        if problem is not None:
            failures.append("reference {}: {}".format(name, problem))
            continue
        _ref_missing(root)
        (root / REFERENCE_DIR / "aiqt" / "zz-orphan.md").write_bytes(b"# Orphan\n")
        (outside / "zz-orphan.md").write_bytes(b"held outside\n")
        outside_before = _tree_bytes(outside)
        scan = module._reference_scan

        def swapped(root_arg, desired, _scan=scan, _root=root, _outside=outside):
            found = _scan(root_arg, desired)
            family = _root / REFERENCE_DIR / "aiqt"
            family.rename(_root / ".aiqt" / "reference" / "aiqt-moved")
            family.symlink_to(_outside)
            return found
        module._reference_scan = swapped
        try:
            got = run_quiet(root, check, runner=module.run)
        finally:
            module._reference_scan = scan
        if got != 2 or _tree_bytes(outside) != outside_before:
            failures.append("reference {}: expected exit 2 with the outside directory unchanged; got {!r}, outside "
                            "unchanged {}".format(name, got, _tree_bytes(outside) == outside_before))
    # The same for the read tree: its aiqt family swapped for a symlink to an outside directory after the read-tree
    # scan (a stand-in for module._rules_scan does the swap) is exit 2 in both modes with the outside unchanged.
    for mode, check in (("check", True), ("write", False)):
        name = "rules-swap-after-scan-" + mode
        root = _reference_root(base, name)
        outside = base / ("outside-" + name)
        outside.mkdir()
        problem = not_generated(root)
        if problem is not None:
            failures.append("reference {}: {}".format(name, problem))
            continue
        (root / RULES_DIR / _RULE_REL).unlink()
        (root / RULES_DIR / "aiqt" / "zz-orphan.md").write_bytes(b"# Orphan\n")
        (outside / "zz-orphan.md").write_bytes(b"held outside\n")
        outside_before = _tree_bytes(outside)
        rules_scan = module._rules_scan

        def rules_swapped(root_arg, desired, _scan=rules_scan, _root=root, _outside=outside):
            found = _scan(root_arg, desired)
            family = _root / RULES_DIR / "aiqt"
            family.rename(_root / RULES_DIR / "aiqt-moved")
            family.symlink_to(_outside)
            return found
        module._rules_scan = rules_swapped
        try:
            got = run_quiet(root, check, runner=module.run)
        finally:
            module._rules_scan = rules_scan
        if got != 2 or _tree_bytes(outside) != outside_before:
            failures.append("reference {}: expected exit 2 with the outside directory unchanged; got {!r}, outside "
                            "unchanged {}".format(name, got, _tree_bytes(outside) == outside_before))
    # A platform that cannot list a directory descriptor (stood in for by taking os.scandir out of os.supports_fd
    # for the two runs): exit 2 in both modes with nothing written, never a path-name fallback or a traceback.
    if os.scandir in os.supports_fd:
        root = _reference_root(base, "no-dir-fd")
        before = _tree_bytes(root)
        os.supports_fd.discard(os.scandir)
        try:
            got = (run_quiet(root, True, runner=module.run), run_quiet(root, False, runner=module.run))
        finally:
            os.supports_fd.add(os.scandir)
        if got != (2, 2) or _tree_bytes(root) != before:
            failures.append("reference no-dir-fd: expected exit 2 in --check and in write mode with nothing written; "
                            "got {!r}, tree unchanged {}".format(got, _tree_bytes(root) == before))
    return failures


# Red on revert for the reference tree: each guard put back makes its named reference case fail, and fail by its
# verdict (an exit, a byte or a tree compare), never because the mutant raised. The no-follow revert puts back
# path-name reads, writes and pruning that follow links together with a scan that follows them (the code before
# the descriptor-bound walk); the descriptor-io revert puts back only the path-name reads, writes and pruning.
# (name, fixed text, reverted text, the case that must fail)
_REF_FOLLOWING_IO = (
    ("            held[rel] = _read_target(root, REFERENCE_DIR + \"/\" + rel)\n",
     "                    _write_target(root, REFERENCE_DIR + \"/\" + rel, want)\n",
     "                _tree_prune(root, REFERENCE_DIR, orphan)\n"),
    ("            held[rel] = (root / REFERENCE_DIR / rel).read_bytes() if (root / REFERENCE_DIR / rel).is_file() "
     "else None\n",
     "                    (root / REFERENCE_DIR / rel).parent.mkdir(parents=True, exist_ok=True)\n"
     "                    (root / REFERENCE_DIR / rel).write_bytes(want)\n",
     "                (root / REFERENCE_DIR / orphan).unlink() if not (root / REFERENCE_DIR / orphan).is_dir() "
     "else (root / REFERENCE_DIR / orphan).rmdir()\n"))
# The read tree's descriptor-bound reads, writes and pruning (fixed), and the path-name forms they replaced, which
# follow links (reverted).
_RULES_FOLLOWING_IO = (
    ("            raw = _read_target(root, RULES_DIR + \"/\" + rel)\n",
     "                    _write_target(root, RULES_DIR + \"/\" + rel, content.encode(\"utf-8\"))\n",
     "                _tree_prune(root, RULES_DIR, orphan)\n"),
    ("            raw = (out_dir / rel).read_bytes() if (out_dir / rel).exists() else None\n",
     "                    (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)\n"
     "                    (out_dir / rel).write_bytes(content.encode(\"utf-8\"))\n",
     "                (out_dir / orphan).unlink()\n"))
# The read-tree scan (fixed), and the listing it replaced, which follows a linked .claude/, RULES_DIR or family
# (reverted).
_RULES_FOLLOWING_SCAN = (
    "        rules_orphans = _rules_scan(root, desired)\n",
    "        rules_orphans = sorted(p.relative_to(out_dir).as_posix() for fam in RULES_FAMILIES "
    "if (out_dir / fam).is_dir() for p in (out_dir / fam).rglob(\"*.md\") "
    "if p.is_file() and p.relative_to(out_dir).as_posix() not in desired)\n")
_REFERENCE_REVERTS = (
    ("reference-byte-compare", "            if held[rel] != want:\n",
     "            if (held[rel] or b\"\").replace(b\"\\r\", b\"\") != want:\n", "crlf"),
    ("reference-write", "                    _write_target(root, REFERENCE_DIR + \"/\" + rel, want)\n",
     "                    pass\n", "missing"),
    ("reference-orphan", "drift.append(\"orphan \" + REFERENCE_DIR + \"/\" + orphan)", "pass", "extra-md"),
    ("reference-orphan-dir", "                if every_name and sub not in keep:\n", "                if False:\n",
     "empty-subdir"),
    ("reference-entry-kind", "                raise _tree_entry_refusal(tree, sub, st)\n", "                pass\n",
     "orphan-symlink"),
    ("reference-stray", "        if strays:\n", "        if False:\n", "stray-file"),
    ("reference-sibling",
     "                strays[:0] = sorted(parent_dir + \"/\" + entry.name for entry in listed if entry.name != own)\n",
     "                pass\n", "sibling-file"),
    ("rules-scan", _RULES_FOLLOWING_SCAN[0], _RULES_FOLLOWING_SCAN[1], "rules-subdir-link-in-root"),
    ("rules-no-follow", (_RULES_FOLLOWING_SCAN[0],) + _RULES_FOLLOWING_IO[0],
     (_RULES_FOLLOWING_SCAN[1],) + _RULES_FOLLOWING_IO[1], "rules-family-link-in-root"),
    ("rules-descriptor-io", _RULES_FOLLOWING_IO[0], _RULES_FOLLOWING_IO[1], "rules-swap-after-scan-write"),
    ("rules-entry-kind", "                raise _tree_entry_refusal(tree, sub, st)\n", "                pass\n",
     "rules-orphan-symlink"),
    ("reference-no-follow", ("_ref_open = _open_dir_component\n",) + _REF_FOLLOWING_IO[0],
     ("_ref_open = lambda fd, part, _rel, _upto: os.open(part, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)\n",)
     + _REF_FOLLOWING_IO[1], "family-link-in-root"),
    ("reference-descriptor-io", _REF_FOLLOWING_IO[0], _REF_FOLLOWING_IO[1], "swap-after-scan-write"),
    ("reference-dir-fd-guard", ("    _require_dir_fd()\n",
                                "    if (os.scandir not in os.supports_fd or os.unlink not in os.supports_dir_fd\n"),
     ("    pass\n", "    if False and (os.scandir not in os.supports_fd or os.unlink not in os.supports_dir_fd\n"),
     "no-dir-fd"),
)


def _named_case_failed(failures, case):
    """True when a failure line names case (a 'reference <case>:' line) and none of its lines records an
    exception from the module under test ('raised ...', run_quiet's sentinel)."""
    named = [line for line in failures if line.startswith("reference {}:".format(case))]
    return bool(named) and not any("'raised " in line for line in named)


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
        # A source path that is there but is not a directory is exit 2 in write mode with the generated
        # target kept, never read as an absent corpus that prunes every generated file as an orphan.
        nondir = tmp / "nondir"
        kept = nondir / ".claude" / "rules" / _RULE_REL
        kept.parent.mkdir(parents=True)
        kept.write_text(_RULE_SRC, encoding="utf-8")
        (nondir / ".aiqt" / "core").mkdir(parents=True)
        (nondir / ".aiqt" / "core" / "rules").write_text("not a directory\n", encoding="utf-8")
        if run_quiet(nondir, check=False) != 2 or not kept.exists():
            failures.append("a source path that is a regular file expected exit 2 with the generated "
                            "target kept")
        # A source path that is a symlink: to a directory inside the root holding the rule, it is read (--check
        # exit 0, the generated target in sync); dangling, to a regular file inside the root, or to an empty
        # directory outside the root, it is exit 2 in write mode with the generated target kept, never read as
        # an absent corpus or followed to one that prunes it as an orphan.
        (tmp / "outside").mkdir()
        for shape, check, want in (("in-root", True, 0), ("dangling", False, 2), ("to-file", False, 2),
                                   ("outside", False, 2)):
            linked = tmp / ("linked-" + shape)
            kept = linked / ".claude" / "rules" / _RULE_REL
            kept.parent.mkdir(parents=True)
            kept.write_text(_RULE_SRC, encoding="utf-8")
            # The reference copy too, so the in-root --check case is in sync in both trees.
            (linked / REFERENCE_DIR / _RULE_REL).parent.mkdir(parents=True)
            (linked / REFERENCE_DIR / _RULE_REL).write_bytes(_RULE_SRC.encode("utf-8"))
            (linked / "shared" / "rules").mkdir(parents=True)
            (linked / "shared" / "rules" / "gen-rules-selftest-target.md").write_text(_RULE_SRC, encoding="utf-8")
            (linked / "shared" / "file.txt").write_text("not a directory\n", encoding="utf-8")
            (linked / ".aiqt" / "core").mkdir(parents=True)
            target = {"in-root": Path("..", "..", "shared", "rules"), "dangling": Path("absent"),
                      "to-file": Path("..", "..", "shared", "file.txt"), "outside": tmp / "outside"}[shape]
            (linked / ".aiqt" / "core" / "rules").symlink_to(target)
            got = run_quiet(linked, check=check)
            if got != want or not kept.exists():
                failures.append("a source path that is a symlink {} expected exit {} with the generated target "
                                "kept, got {}".format(shape, want, got))
        # The .claude/ directory above the read tree, the read tree, or a family directory in it, that is a
        # symlink to a directory holding a planted orphan and lacking the generated rule, in write mode: outside
        # the root or inside it, it is exit 2 with the orphan kept and the rule not written there, never followed
        # to prune or write (the descriptor walk refuses a link wherever it points).
        for linked_rel, sub in ((".claude", ("rules", "aiqt")), (".claude/rules", ("aiqt",)),
                                (".claude/rules/aiqt", ()), (".claude/rules/security", ())):
            for where, want in (("outside", 2), ("in-root", 2)):
                out = tmp / ("out-" + linked_rel.lstrip(".").replace("/", "-") + "-" + where)
                (out / ".aiqt" / "core" / "rules").mkdir(parents=True)
                (out / ".aiqt" / "core" / "rules" / "gen-rules-selftest-target.md").write_text(
                    _RULE_SRC, encoding="utf-8")
                link = out / linked_rel
                link.parent.mkdir(parents=True, exist_ok=True)
                held = tmp / ("held-" + out.name) if where == "outside" else out / "shared" / "held"
                fam = held.joinpath(*sub)
                fam.mkdir(parents=True)
                (fam / "zz-planted-orphan.md").write_text("planted\n", encoding="utf-8")
                link.symlink_to(held if where == "outside" else os.path.relpath(held, link.parent))
                got = run_quiet(out, check=False)
                kept = (fam / "zz-planted-orphan.md").exists()
                wrote = (fam / Path(_RULE_REL).name).exists()
                if (got, kept, wrote) != (want, want == 2, want == 0 and linked_rel != ".claude/rules/security"):
                    failures.append("a {} that is a symlink {} expected exit {} with the orphan {} there, "
                                    "got exit {}, orphan kept {}, rule written {}".format(
                                        linked_rel, where, want, "kept and nothing written" if want == 2
                                        else "pruned", got, kept, wrote))

        # The two-layer split: a well-formed detail rule generates and then checks clean, its generated
        # file the whole source byte for byte when it has no split, and otherwise its unsplit render
        # (_unsplit_reference, a second implementation of full_body); every malformed layout is a malformed
        # source (exit 2).
        detail_base = tmp / "detail"
        for name, _keys, _body, expected in _DETAIL_CASES:
            root = _detail_case_root(detail_base, name)
            got = run_quiet(root, check=False)
            if got != expected:
                failures.append("detail case {}: expected exit {}, got {!r}".format(name, expected, got))
            elif expected == 0:
                source = root / ".aiqt" / "core" / "rules" / "gen-rules-selftest-detail.md"
                generated = root / ".claude" / "rules" / derive(parse_source(source), source.name)
                raw = source.read_bytes()
                want = raw if b"\n" + DETAIL_HEADING.encode("ascii") + b"\n" not in raw else \
                    _unsplit_reference(raw.decode("utf-8")).encode("utf-8")
                if not generated.is_file() or generated.read_bytes() != want:
                    failures.append("detail case {}: the generated file is not {} byte for byte".format(
                        name, "the source" if want == raw else "the source's unsplit render"))
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

        # The split is invisible in the read tree: every split source renders byte-equal to its unsplit
        # original in write and check mode (_SPLIT_CASES), and each split-render guard put back goes red.
        failures.extend(_split_render_failures(sys.modules[__name__], tmp / "split", run_quiet))
        for label, old, new in _SPLIT_REVERTS:
            try:
                mutant = _load_reverted(revert_base, label, old, new)
            except AssertionError as exc:
                failures.append(str(exc))
                continue
            if not _split_render_failures(mutant, revert_base / label, run_quiet):
                failures.append("revert {}: with the guard removed, the split cases expected at least one failure, "
                                "got none".format(label))

        # The generated trees: the reference tree is generated as each source's full render; a missing, edited,
        # extra, CRLF-altered, lone-CR, non-UTF-8 or removed reference tree, or an empty or orphaned subdirectory,
        # is drift with --check writing nothing; a malformed source, a linked reference target and a reference
        # directory linked outside the root (3 cases), every _REFERENCE_REFUSED_CASES entry (a link at or in a
        # reference or read-tree family, REFERENCE_DIR or RULES_DIR linked inside the root, an orphan or target
        # that is a link, a FIFO, a stray entry beside the families or beside REFERENCE_DIR), a family of either
        # tree swapped for a link after its scan (4 cases) and a platform that cannot list a directory descriptor
        # (1 case) are exit 2 with nothing written or deleted; and each guard put back makes its named case fail.
        (tmp / "reference").mkdir()
        failures.extend(_reference_failures(sys.modules[__name__], tmp / "reference", run_quiet))
        for label, old, new, case in _REFERENCE_REVERTS:
            try:
                mutant = _load_reverted(revert_base, label, old, new)
            except AssertionError as exc:
                failures.append(str(exc))
                continue
            (revert_base / label).mkdir()
            if not _named_case_failed(_reference_failures(mutant, revert_base / label, run_quiet), case):
                failures.append("revert {}: with the guard removed, reference case {} expected to fail by its verdict "
                                "(not by an exception), and did not".format(label, case))

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
        # when ADOPTER MODE is made to drop a key (here, by skipping the facet line as a comment) or to
        # alter a value (the slug); the strict comparison goes red when the strict read alters a value.
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
            try:
                mutant = _load_reverted(revert_base, "adopter-yaml-alter-value", *_ADOPTER_ALTER_VALUE)
            except AssertionError as exc:
                failures.append(str(exc))
            else:
                altered = mutant._yaml_adopter_agreement(adopter_accepted)
                if len([f for f in altered if ": slug: " in f and f.endswith(" to YAML")]) != len(adopter_accepted):
                    failures.append("revert adopter-yaml-alter-value: with ADOPTER MODE altering the slug value, "
                                    "the PyYAML comparison expected a value difference for each of the {} "
                                    "adopter-mode case(s), got {!r}".format(len(adopter_accepted), altered))
        if agreement is not None:
            try:
                mutant = _load_reverted(revert_base, "strict-yaml-alter-value", *_STRICT_ALTER_VALUE)
            except AssertionError as exc:
                failures.append(str(exc))
            else:
                (tmp / "strict-value").mkdir()
                altered = mutant._yaml_agreement(repo_root(), tmp / "strict-value")
                if len([f for f in altered[0] if ": slug: " in f and f.endswith(" to YAML")]) != sum(altered[1:3]):
                    failures.append("revert strict-yaml-alter-value: with the strict read altering the slug value, "
                                    "the PyYAML comparison expected a value difference for each of the {} live "
                                    "and fuzzed source(s), got {!r}".format(sum(altered[1:3]), altered[0][:5]))

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
    failures.extend(_map_keys_refusal_failures())
    # An integer value is ASCII digits only: Arabic-Indic and fullwidth digits are refused, never kept
    # as a string or read as the int that int() would make of them.
    for text in ("١٢", "１２", "-١", "1٢"):
        try:
            got = _value("tier", text)
        except ValueError as exc:
            if "non-ASCII digits" not in str(exc):
                failures.append("the non-ASCII digit value {} expected the non-ASCII digits error, got {}"
                                .format(ascii(text), exc))
        else:
            failures.append("the non-ASCII digit value {} expected a refusal, got {!r}".format(
                ascii(text), got))
    # Under the typed FRONTMATTER grammar an integer is a declared type (tier, a bare corpus-id), never a
    # guess: a text value stays a string, an ASCII tier is an int, and a signed or dashed number is no tier.
    if _value("detail-trigger", "v12") != "v12":
        failures.append("the text value v12 expected to stay a string, got {!r}".format(
            _value("detail-trigger", "v12")))
    if _value("tier", "10") != 10:
        failures.append("the ASCII tier 10 expected the int 10, got {!r}".format(_value("tier", "10")))
    for text in ("-", "--1", "-12"):
        try:
            got = _value("tier", text)
        except ValueError:
            continue
        failures.append("the tier value {} expected a refusal, got {!r}".format(ascii(text), got))

    # An unknown option is exit 2 with the usage line and runs nothing (red when main()'s guard is reverted).
    failures.extend(_argv_guard_failures(globals(), "gen_rules.py"))
    # A valued option never takes a following flag as its value (--root --chek is refused, not run).
    failures.extend(_valued_option_failures())
    # Every committed tools/gen_*.py refuses an unknown option with exit 2 and its usage line, writing nothing (red
    # when any one committed generator's guard is reverted); on a tree made stale on purpose its no-argument run
    # ends at a write in the copy and its --check run exits 1 with nothing blocked; it runs in a throwaway copy of
    # HEAD, never in the working tree.
    failures.extend(_gen_argv_class_check_failures())
    # The class check's real-checkout read still copies a checkout owned by another uid, trusting that checkout
    # alone (red when the read's safe.directory entry is removed).
    failures.extend(_gen_argv_foreign_owner_failures())
    # A checkout whose resolved path git would read as a pattern (ending in `/*`, or holding `*`) is refused before
    # any git call (red when _gen_argv_safe_directory's refusal is removed).
    failures.extend(_gen_argv_glob_root_failures())
    # The class check's copy gives HEAD its first parent's tree as a parent commit, so gen_rules_core.py --check in a
    # copy passes on a clean change and fails on an undo (red when the parent read is switched off: exit 2 both).
    failures.extend(_gen_argv_first_parent_failures())

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: an invalid-UTF-8 generated target fails closed (exit 2), not a raw "
          "UnicodeDecodeError traceback (guards the widened reconcile arm); a source path that is a regular "
          "file or a symlink that is dangling, to a regular file or to a directory outside the root exits 2 "
          "with the generated target kept, and one symlinked to a directory inside the root is read; a "
          ".claude/, .claude/rules or family directory symlinked outside or inside the root exits 2 in write mode "
          "with nothing written or deleted there; the MAP_KEYS read refusal "
          "exits 2 with empty stdout under {} stderr conditions ({}); {} detail layout case(s) "
          "hold and {} guard revert(s) each go red; {} split source(s) render byte-equal to their unsplit "
          "original in write and check mode, and {} split-render revert(s) each go red; the reference tree "
          "holds each source's full render, {} reference drift case(s) each exit 1 in --check with nothing "
          "written and are repaired in write mode, {} generated-tree refusal case(s) (a malformed source, a "
          "linked reference target, a reference directory linked outside the root, {} link, FIFO or stray "
          "case(s) across the reference and read trees, 4 swap-after-scan cases and a platform that cannot list "
          "a directory descriptor) each exit 2 with nothing written or deleted, and {} generated-tree "
          "revert(s) each go red; an adopter-shaped "
          "rule with a non-ASCII value is accepted and goes red under the ASCII-only revert; {} parse-level "
          "revert(s) each go red; {} adopter-mode case(s) pass the placement gate and are refused by the pack reader, {} more are refused by both, "
          "and {} adopter-mode revert(s) each go red; an unknown option is exit 2 with the usage line and runs "
          "nothing, here and in every tools/gen_*.py, each of which, on a tree made stale on purpose, ends at a "
          "write in its copy with no argument and exits 1 with nothing blocked under --check, and a valued option "
          "never takes a following flag as its value; the class check's copy commits HEAD's first parent's tree "
          "under HEAD's, so gen_rules_core.py --check in a copy passes on a clean change and fails on an undo "
          "(each exit 2 with the first parent not committed); "
          "{}.".format(
              len(_MAP_KEYS_CONDITIONS), ", ".join(_MAP_KEYS_CONDITIONS),
              len(_DETAIL_CASES), len(_DETAIL_REVERTS) + 1, len(_SPLIT_CASES), len(_SPLIT_REVERTS),
              len(_REFERENCE_DRIFT_CASES), 3 + len(_REFERENCE_REFUSED_CASES) + 4 + (os.scandir in os.supports_fd),
              len(_REFERENCE_REFUSED_CASES), len(_REFERENCE_REVERTS), len(_PARSE_REVERTS), len(_ADOPTER_MODE_CASES),
              len(_ADOPTER_MODE_REFUSALS), len(_ADOPTER_MODE_REVERTS),
              "PyYAML agreement NOT RUN (PyYAML is not importable)" if agreement is None else
              "PyYAML safe_load reads the same keys and values for {} live source(s), {} fuzzed "
              "frontmatter(s) ({} disclosed bare float id(s)) and {} adopter-mode case(s) read in ADOPTER "
              "MODE (red when either read alters a value and when ADOPTER MODE drops a key)".format(
                  *agreement[1:], len(_ADOPTER_MODE_CASES))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
