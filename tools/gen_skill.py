#!/usr/bin/env python3
"""Generate the universal AIQT chat-assistant skill from a single source, drift-gated like the others.

The shipped chat artefacts (the SKILL.md the zip carries, the aiqt-instructions.txt fallback) were
hand-maintained copies sitting one layer above the gated rule corpus, with nothing checking them
against it. This generator closes that gap the same way the rest of the pack is closed: one canonical
source, .aiqt/core/skill/skill-source.md, carries the distilled chat standard (the apex, the four
facet definitions, the five-rule surfacing loop, and the chat-applicable security cut), and every
security rule and the apex are cited BY corpus-id and resolved against the live .aiqt/core/rules/
corpus (via gen_rules.load_corpus). An id that does not resolve is a fail-closed exit 2: the skill can
never cite a rule that does not exist. The rendered body is time-independent (the build date lives
only in the manifest and provenance), so --check is a pure byte comparison like the sibling gates.

Outputs (all under the reserved site/downloads/aiqt/ subtree, plus the standalone instructions file):
  site/downloads/aiqt/SKILL.md        the generated skill body (the text the zip carries)
  site/downloads/aiqt/manifest.json   version, date, source-corpus hash, included corpus-ids
  site/downloads/aiqt/provenance.md   human-readable provenance for the same facts
  site/downloads/aiqt-instructions.txt  the same body wrapped in the no-Skills-feature preamble
  site/downloads/aiqt-skill.zip       the public download, packed deterministically from that SKILL.md

  gen_skill.py            regenerate every output
  gen_skill.py --check    fail (exit 1) on drift; exit 2 on a malformed source or an unknown corpus-id
  gen_skill.py --self-test  prove the gate fails on drift, an unknown id, an orphan output, a bad target
"""
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: gen_skill.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile  # noqa: E402
from _standards import dir_present  # noqa: E402
from gen_rules import load_corpus  # noqa: E402

# The canonical apex ordering. It MUST appear verbatim in the apex rule body (prjint1), so the skill's
# top-line ordering is tied to the corpus and cannot silently drift from it.
CANON_ORDERING = "(Accuracy = Integrity = Quality = Trust) > Progress > Speed > Cost"
# The security family's CIA-plus-privacy order, matching gen_agents.CIA_FACET_ORDER, so the rendered
# security entries sort deterministically the same way the rest of the pack orders that family.
CIA_FACET_ORDER = {"SECC": 0, "SECI": 1, "SECA": 2, "SECP": 3}
# AIQT-facet order for the conduct block (accuracy, integrity, quality, trust, progress), so the
# non-security conduct rules render in a stable, facet-grouped order the same way the security block does.
AIQT_FACET_ORDER = {"ACCUR": 0, "INTEG": 1, "QUALI": 2, "TRUST": 3, "PROGR": 4}

# Output locations, as relative parts joined under the repo root (or a --self-test temp root).
RESERVED_PARTS = ("site", "downloads", "aiqt")            # 100% generated: orphan-scanned in full
INSTRUCTIONS_PARTS = ("site", "downloads", "aiqt-instructions.txt")  # a standalone named output
ZIP_PARTS = ("site", "downloads", "aiqt-skill.zip")       # a standalone named BINARY output: the stable
# "latest" alias, kept byte-identical to the version-numbered copy so a direct link never breaks across
# releases. The site links to the version-numbered copy; both are written from the same bytes, so
# gen_skill --check (which compares each to disk) keeps the two byte-identical.
ZIP_VERSIONED_PARTS = ("site", "downloads", "aiqt-skill-1.0.4.zip")  # the version-numbered copy the site
# links to. The literal version here is tied to the skill meta version (skill-source.md) by a fail-closed
# assertion in build_outputs, so a skill bump that forgets to update this name fails closed.
SKILL_SRC_PARTS = (".aiqt", "core", "skill", "skill-source.md")
CORPUS_PARTS = (".aiqt", "core", "rules")
# The single canonical operator-identity source (the same file the hooks generator and the portability
# gate read from). The public attribution line (GD-56) is built from the [plugin] author-name here plus
# the pack's public source URL, so the maintainer's name is never a literal in any scanned source file;
# it enters the two download artefacts only, where the portability gate carries its narrow exemption.
IDENTITY_MANIFEST_PARTS = (".aiqt", "core", "hooks", "manifest.toml")
ATTRIBUTION_SOURCE_URL = "https://github.com/jposluns/guardrails"

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py; VER-CORE 6.5).
RENDERER_DECL = {"renderer-id": "skill", "semantics-revision": 2}
# GENSRC_OUTPUTS is STATICALLY parsed by gen_gensrc.py and must be a LITERAL (a tuple of dict literals),
# so each source list is inlined rather than shared through a name. The hooks manifest is a content-bearing
# source: the public attribution line (GD-56) is rendered from its [plugin] author-name, so a change to that
# name changes these outputs and must re-trigger regeneration.
GENSRC_OUTPUTS = (
    {"target": "site/downloads/aiqt/", "kind": "tree",
     "sources": (".aiqt/core/skill/skill-source.md", ".aiqt/core/rules/",
                 ".aiqt/core/hooks/manifest.toml"),
     "regenerate": "python3 tools/gen_skill.py"},
    {"target": "site/downloads/aiqt-instructions.txt", "kind": "file",
     "sources": (".aiqt/core/skill/skill-source.md", ".aiqt/core/rules/",
                 ".aiqt/core/hooks/manifest.toml"),
     "regenerate": "python3 tools/gen_skill.py"},
    {"target": "site/downloads/aiqt-skill.zip", "kind": "file",
     "sources": (".aiqt/core/skill/skill-source.md", ".aiqt/core/rules/",
                 ".aiqt/core/hooks/manifest.toml"),
     "regenerate": "python3 tools/gen_skill.py"},
    {"target": "site/downloads/aiqt-skill-1.0.4.zip", "kind": "file",
     "sources": (".aiqt/core/skill/skill-source.md", ".aiqt/core/rules/",
                 ".aiqt/core/hooks/manifest.toml"),
     "regenerate": "python3 tools/gen_skill.py"},
)
# The install-page SKILL-DOWNLOAD block (site/install.html) is generated and drift-gated by its own
# generator, tools/gen_install.py, a block generator with NO RENDERER_DECL registered in the gensrc
# registry the same way gen_disclosure.py registers site/disclosure.html. Keeping it a separate,
# RENDERER_DECL-free generator is what lets it be roster-tracked without gen_manifest recording the whole
# hand-authored install page as a whole-file "skill" artifact.

# The public download is packed deterministically so its bytes never depend on the wall clock, the host
# OS, or the zlib version: a fixed ZIP-epoch timestamp, members in sorted order, a fixed unix mode, and
# STORED (uncompressed) entries. STORED also keeps the member text in the clear, so the leak gates read
# the real SKILL.md rather than an opaque compressed blob, and --check can byte-compare the archive.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
ZIP_MEMBER = "SKILL.md"

_SECTION = re.compile(r"^=== (\S+) ===$")
_ENTRY = re.compile(r"^\[([a-z0-9]{6,})\]$")
_META = re.compile(r"^([a-z0-9-]+):\s*(.*)$")

REQUIRED_SECTIONS = ("meta", "description", "instructions-preamble", "body-aiqt", "body-rules",
                     "conduct-intro", "conduct-unconditional", "conduct-conditional",
                     "security-intro", "security-unconditional", "security-conditional",
                     "security-capability-note")
REQUIRED_META = ("name", "version", "license", "date", "apex-id")


def _split_sections(text):
    """Split the source into named `=== name ===` sections. A line before the first header is an error
    (nothing should sit outside a section), caught as a ValueError so a malformed source is exit 2."""
    sections = {}
    current = None
    buf = []
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip("\n")
            current = m.group(1)
            if current in sections:
                raise ValueError("duplicate section '{}' in skill source".format(current))
            buf = []
        else:
            if current is None:
                if line.strip():
                    raise ValueError("content before the first '=== section ===' in skill source")
                continue
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip("\n")
    return sections


def _parse_meta(block):
    meta = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        m = _META.match(line)
        if not m:
            raise ValueError("bad meta line {!r} in skill source".format(line))
        key, val = m.group(1), m.group(2).strip()
        if key in meta:
            raise ValueError("duplicate meta key '{}' in skill source".format(key))
        meta[key] = val
    return meta


def _parse_entries(block, label):
    """Parse a security block into an ordered list of (corpus-id, distilled-text) pairs. Any text before
    the first [corpus-id] marker is a malformed source (exit 2)."""
    entries = []
    cid = None
    buf = []
    for line in block.splitlines():
        m = _ENTRY.match(line)
        if m:
            if cid is not None:
                entries.append((cid, "\n".join(buf).strip("\n")))
            cid = m.group(1)
            buf = []
        else:
            if cid is None:
                if line.strip():
                    raise ValueError("text before the first [corpus-id] in {} block".format(label))
                continue
            buf.append(line)
    if cid is not None:
        entries.append((cid, "\n".join(buf).strip("\n")))
    if not entries:
        raise ValueError("no rule entries in {} block".format(label))
    for c, txt in entries:
        if not txt:
            raise ValueError("empty distilled text for '{}' in {} block".format(c, label))
    return entries


def parse_source(path):
    """Read and structure skill-source.md. Raises ValueError on any malformed shape and lets an OSError
    (an unreadable or absent required source) propagate: both become a fail-closed exit 2 in the caller."""
    text = path.read_text(encoding="utf-8")
    sections = _split_sections(text)
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        raise ValueError("skill source missing section(s): {}".format(", ".join(missing)))
    meta = _parse_meta(sections["meta"])
    missing_meta = [k for k in REQUIRED_META if k not in meta]
    if missing_meta:
        raise ValueError("skill source meta missing key(s): {}".format(", ".join(missing_meta)))
    return {
        "meta": meta,
        "description": sections["description"],
        "preamble": sections["instructions-preamble"],
        "body_aiqt": sections["body-aiqt"],
        "body_rules": sections["body-rules"],
        "security_intro": sections["security-intro"],
        "capability_note": sections["security-capability-note"],
        "conduct_intro": sections["conduct-intro"],
        "conduct_unconditional": _parse_entries(sections["conduct-unconditional"], "conduct-unconditional"),
        "conduct_conditional": _parse_entries(sections["conduct-conditional"], "conduct-conditional"),
        "unconditional": _parse_entries(sections["security-unconditional"], "security-unconditional"),
        "conditional": _parse_entries(sections["security-conditional"], "security-conditional"),
    }


def _rule_body(path):
    """The rule body with its YAML frontmatter stripped (same extraction as gen_agents.body_of, without
    the H1 demotion). Used only for the deterministic source-corpus hash, never rendered into the body."""
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    return text[end + 5:].strip()


def resolve(source, corpus):
    """Tie the source to the live corpus: resolve every cited corpus-id, fail closed on an unknown or
    duplicated one (the anti-fabrication gate), verify the apex still carries the canonical ordering, and
    compute the deterministic source-corpus hash over the included rules. Returns a render-ready dict.

    Raises ValueError on any unresolved/duplicate id or a drifted apex, so the caller exits 2."""
    by_id = {}
    for src, fm, _rel in corpus:
        by_id[str(fm["corpus-id"])] = (fm, _rule_body(src))

    apex_id = source["meta"]["apex-id"]
    if apex_id not in by_id:
        raise ValueError("skill source cites unknown corpus-id '{}' (apex)".format(apex_id))
    if CANON_ORDERING not in by_id[apex_id][1]:
        raise ValueError("apex rule '{}' no longer carries the canonical AIQT ordering".format(apex_id))

    included = [apex_id]

    def resolve_group(entries, group, facet_order):
        out = []
        for cid, text in entries:
            if cid not in by_id:
                raise ValueError("skill source cites unknown corpus-id '{}'".format(cid))
            if cid in included:
                raise ValueError("skill source cites corpus-id '{}' more than once".format(cid))
            included.append(cid)
            fm = by_id[cid][0]
            out.append((cid, text, fm.get("facet", ""), str(fm.get("slug", ""))))
        # A rule's facet must belong to this block's facet set: a security rule may not sit in the
        # conduct block, nor a conduct rule in the security block (silent misplacement guard).
        for _cid, _text, _facet, _slug in out:
            if _facet not in facet_order:
                raise ValueError("skill source places '{}' (facet {}) in the {} block, which does not "
                                 "accept that facet".format(_cid, _facet or "none", group))
        # Deterministic facet order (per the passed facet_order), then slug, matching gen_agents.
        out.sort(key=lambda e: (facet_order[e[2]], e[3]))
        return [text for _cid, text, _f, _s in out]

    conduct_uncond_texts = resolve_group(source["conduct_unconditional"], "conduct-unconditional", AIQT_FACET_ORDER)
    conduct_cond_texts = resolve_group(source["conduct_conditional"], "conduct-conditional", AIQT_FACET_ORDER)
    uncond_texts = resolve_group(source["unconditional"], "unconditional", CIA_FACET_ORDER)
    cond_texts = resolve_group(source["conditional"], "conditional", CIA_FACET_ORDER)

    digest = hashlib.sha256()
    for cid in sorted(included):
        digest.update("{}\n{}\n".format(cid, by_id[cid][1]).encode("utf-8"))

    return {
        "meta": source["meta"],
        "description": source["description"],
        "preamble": source["preamble"],
        "body_aiqt": source["body_aiqt"],
        "body_rules": source["body_rules"],
        "security_intro": source["security_intro"],
        "capability_note": source["capability_note"],
        "conduct_intro": source["conduct_intro"],
        "conduct_uncond_texts": conduct_uncond_texts,
        "conduct_cond_texts": conduct_cond_texts,
        "uncond_texts": uncond_texts,
        "cond_texts": cond_texts,
        "included_ids": sorted(included),
        "corpus_hash": "sha256:" + digest.hexdigest(),
    }


def _frontmatter(data):
    # Only `name` and `description` (matching the cleanlanguage skill). The version and licence are NOT
    # frontmatter keys: they are carried in the visible `# AIQT™` header block below. A sanitizing skill
    # viewer that surfaces the tail of the frontmatter would otherwise render the `license`/`version`
    # keys as stray text between the description and that header block, so they are kept out of the
    # frontmatter entirely (the meta version/licence still drive the header line and the zip name).
    desc = "\n".join("  " + line for line in data["description"].splitlines())
    return ("---\n"
            "name: {name}\n"
            "description: >-\n{desc}\n"
            "---").format(name=data["meta"]["name"], desc=desc)


def _conduct_block(data):
    blocks = ["# Conduct", data["conduct_intro"]]
    blocks += data["conduct_uncond_texts"]
    blocks.append("## If your platform exposes tools, browsing, retrieval, or persistent memory")
    blocks += data["conduct_cond_texts"]
    return "\n\n".join(blocks)


def _security_block(data):
    blocks = ["# Security", data["security_intro"]]
    blocks += data["uncond_texts"]
    blocks.append("## If your platform exposes tools, browsing, retrieval, or persistent memory")
    blocks += data["cond_texts"]
    blocks.append(data["capability_note"])
    return "\n\n".join(blocks)


def _header_block(data):
    """The visible metadata block (cleanlanguage-style) placed directly under the SKILL.md `# AIQT™` H1:
    five fields, each on its own rendered line. The first four lines end in a two-space CommonMark hard
    break (markdownlint MD009 br_spaces=2), exactly as the cleanlanguage skill does, so the block renders
    as five lines even in sanitizing markdown viewers that honour neither a trailing-backslash break nor
    an inline <br>. The shipped-surface byte-canon gate forbids trailing whitespace in general; a single,
    disclosed, path-scoped hard-break allowance (byte-canon.toml [[hardbreak]] for this SKILL.md) permits
    EXACTLY two trailing spaces on a non-blank line and nothing else, so the gate is honoured, not weakened.
    The portability author-header exemption (check_portability.author_header_line) matches this Author line
    by its STRIPPED content, so its two trailing spaces are transparent to that match. Website is the
    [plugin] homepage value verbatim from the identity manifest (e.g. https://aiqt.ai)."""
    return ("Version: {version}  \n"
            "Author: {name}  \n"
            "Website: {homepage}  \n"
            "GitHub: {github}  \n"
            "Licence: CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)").format(
                version=data["meta"]["version"], name=data["identity_name"],
                homepage=data["identity_homepage"], github=ATTRIBUTION_SOURCE_URL)


def render_skill(data):
    blocks = [
        _frontmatter(data),
        "# AIQT™\n\n" + _header_block(data) + "\n\n" + data["body_aiqt"],
        "# Rules\n\n" + data["body_rules"],
        _conduct_block(data),
        _security_block(data),
        # Public attribution footer (GD-56): attributes both the project and the maintainer under the
        # pack's CC BY-SA. The portability gate carries a narrow, reviewed exemption for exactly this line.
        "---\n\n" + data["attribution"],
    ]
    return "\n\n".join(blocks) + "\n"


def versioned_zip_basename(version):
    """The version-numbered download filename for a skill version, e.g. 'aiqt-skill-1.0.3.zip'. This is the
    shared SHAPE helper: it spells the filename PATTERN in one place, so the build-time match assertion, the
    install-page block (gen_install.py), and any other caller derive the name the same way. It is NOT the
    single source of the concrete versioned name: that name is spelled as a literal in several spots (the
    four in RELEASING.md's bump checklist: the skill-source.md meta version, the ZIP_VERSIONED_PARTS literal
    and its GENSRC_OUTPUTS target, check_portability.BINARY_ALLOW, and ownership.toml [checkout].binary).
    Those literals are kept consistent by the fail-closed version-match assertion in build_outputs plus the
    bump checklist, not by true single-sourcing."""
    return "aiqt-skill-{}.zip".format(version)


def zip_versioned_version():
    """The version substring embedded in the ZIP_VERSIONED_PARTS basename ('aiqt-skill-<v>.zip'). Lets the
    self-test and conformance fixtures pin their skill meta version to the shipped literal, so the
    version-match assertion in build_outputs passes for a well-formed fixture and fires only on a mismatch."""
    base = ZIP_VERSIONED_PARTS[-1]
    return base[len("aiqt-skill-"):-len(".zip")]


def render_zip(data):
    """The public aiqt-skill.zip, built in memory from the SAME SKILL.md the reserved subtree carries, so
    the zip's SKILL.md member is byte-identical to the tracked SKILL.md. Deterministic (see ZIP_EPOCH
    note): fixed timestamp, sorted members, STORED compression, fixed unix mode, no wall-clock, so two
    runs produce identical bytes and --check can byte-compare the archive."""
    members = {ZIP_MEMBER: render_skill(data).encode("utf-8")}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3            # unix, fixed so the byte layout never depends on the host OS
            info.external_attr = 0o644 << 16  # -rw-r--r--, fixed rather than inherited from any real file
            zf.writestr(info, members[name])
    return buf.getvalue()


def render_instructions(data):
    header = ("AIQT™: a standard for your AI assistant\n"
              "Version {v} . Licensed under CC BY-SA 4.0 "
              "(https://creativecommons.org/licenses/by-sa/4.0/)\n"
              "{attr}").format(v=data["meta"]["version"], attr=data["attribution"])
    blocks = [
        header,
        data["preamble"],
        "=" * 60,
        "# AIQT\n\n" + data["body_aiqt"],
        "# Rules\n\n" + data["body_rules"],
        _conduct_block(data),
        _security_block(data),
    ]
    return "\n\n".join(blocks) + "\n"


def render_manifest(data):
    obj = {
        "name": data["meta"]["name"],
        "version": data["meta"]["version"],
        "license": data["meta"]["license"],
        "date": data["meta"]["date"],
        "generator": "tools/gen_skill.py",
        "generator-version": "2",
        "source-corpus-hash": data["corpus_hash"],
        "included-rule-ids": data["included_ids"],
    }
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def render_provenance(data):
    m = data["meta"]
    lines = [
        "# AIQT skill provenance",
        "",
        "Generated by tools/gen_skill.py from .aiqt/core/skill/skill-source.md and the "
        ".aiqt/core/rules/ corpus. Do not hand-edit; edit the source and regenerate.",
        "",
        "- Skill: {}".format(m["name"]),
        "- Version: {}".format(m["version"]),
        "- Licence: CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)",
        "- Date: {}".format(m["date"]),
        "- Source corpus hash: {}".format(data["corpus_hash"]),
        "- Included rules (by corpus id): {}".format(", ".join(data["included_ids"])),
        "",
        "The published aiqt-skill.zip and its version-numbered copy aiqt-skill-{}.zip are "
        "byte-identical, packed deterministically from this same SKILL.md by the same generator, so their "
        "SKILL.md member is byte-identical to the text recorded here.".format(m["version"]),
    ]
    return "\n".join(lines) + "\n"


def plugin_identity(root):
    """Read the operator NAME and HOMEPAGE from the [plugin] table of the canonical identity manifest, so
    neither is ever a literal in a scanned source file. Returns (name, homepage). An absent, unparseable,
    name-less, or homepage-less manifest is fail-closed (OSError/ValueError, which build_outputs surfaces
    as exit 2)."""
    path = root.joinpath(*IDENTITY_MANIFEST_PARTS)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    plugin = data.get("plugin") if isinstance(data, dict) else None
    name = plugin.get("author-name") if isinstance(plugin, dict) else None
    homepage = plugin.get("homepage") if isinstance(plugin, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise ValueError("identity manifest {} has no [plugin] author-name".format(
            "/".join(IDENTITY_MANIFEST_PARTS)))
    if not isinstance(homepage, str) or not homepage.strip():
        raise ValueError("identity manifest {} has no [plugin] homepage".format(
            "/".join(IDENTITY_MANIFEST_PARTS)))
    return name.strip(), homepage.strip()


def attribution_string(name):
    """The public attribution line (GD-56), built from the operator name plus the pack's public source URL,
    so the maintainer's name is never a literal in a scanned source file. The exact string must match the
    portability gate's exempt line."""
    return "AIQT Guardrails by {}, {}, CC BY-SA 4.0".format(name, ATTRIBUTION_SOURCE_URL)


def build_outputs(root):
    """Load the corpus and skill source under root and render every output. Returns
    (reserved_map, standalone, binary): reserved_map is {filename: text} for the reserved
    site/downloads/aiqt/ subtree, standalone is [(abs_path, text)] for named text outputs beside it, and
    binary is [(abs_path, bytes)] for named binary outputs beside it (the deterministic download zip).
    The install-page download block is generated by its own generator (tools/gen_install.py), not here.
    Raises ValueError/OSError (an unknown id, a malformed or unreadable source): the caller fails closed.
    Parameterized on root so the conformance suite and the self-test can call it off the real tree."""
    corpus = load_corpus(root.joinpath(*CORPUS_PARTS))
    source = parse_source(root.joinpath(*SKILL_SRC_PARTS))
    data = resolve(source, corpus)
    # Fail-closed version-match gate (runs in CI via gen_skill --check): the shipped version-numbered zip
    # literal MUST spell the skill meta version, so a skill bump that forgets to update ZIP_VERSIONED_PARTS
    # (and the GENSRC_OUTPUTS target beside it) fails closed rather than shipping a stale filename.
    expected_basename = versioned_zip_basename(data["meta"]["version"])
    if ZIP_VERSIONED_PARTS[-1] != expected_basename:
        raise ValueError(
            "versioned zip name {!r} does not match the skill meta version {!r} (expected {!r}); bump "
            "ZIP_VERSIONED_PARTS and its GENSRC_OUTPUTS target when the skill version changes".format(
                ZIP_VERSIONED_PARTS[-1], data["meta"]["version"], expected_basename))
    name, homepage = plugin_identity(root)
    data["identity_name"] = name
    data["identity_homepage"] = homepage
    data["attribution"] = attribution_string(name)
    reserved_map = {
        "SKILL.md": render_skill(data),
        "manifest.json": render_manifest(data),
        "provenance.md": render_provenance(data),
    }
    standalone = [(root.joinpath(*INSTRUCTIONS_PARTS), render_instructions(data))]
    # Both zips are written from the SAME bytes, so the version-numbered copy and the stable "latest" alias
    # are byte-identical by construction; gen_skill --check compares each to disk, so a divergence is caught.
    zip_bytes = render_zip(data)
    binary = [(root.joinpath(*ZIP_PARTS), zip_bytes),
              (root.joinpath(*ZIP_VERSIONED_PARTS), zip_bytes)]
    return reserved_map, standalone, binary


def run_gen(root, check):
    """Reconcile every output under root. Exit 0 in sync, 1 on drift (check mode), 2 on a malformed
    source, an unknown corpus-id, or a read/write failure. Mirrors gen_cursor.main()'s fail-closed shape:
    dir_present (not is_dir) so an unreadable .aiqt/ parent fails closed, os.walk(onerror=raise) (not
    rglob) for the orphan scan so an unreadable output dir fails closed instead of concealing an orphan."""
    corpus_dir = root.joinpath(*CORPUS_PARTS)
    reserved_dir = root.joinpath(*RESERVED_PARTS)
    drift = []

    def _raise(exc):
        raise exc
    try:
        # An absent corpus is a transition state (desired empty): reconcile so stale outputs are removed,
        # never concealed. A PRESENT corpus with a missing/unreadable skill source is malformed (the
        # OSError from parse_source propagates here as exit 2), which is the correct fail-closed outcome.
        if dir_present(corpus_dir):
            reserved_map, standalone, binary = build_outputs(root)
        else:
            reserved_map, standalone, binary = {}, [], []
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2
    try:
        for path, content in standalone:
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != content:
                drift.append(path.relative_to(root).as_posix())
                if not check:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
        # Named binary outputs (the download zip) reconcile on bytes, so a stale or hand-swapped archive
        # is caught by the same drift gate as the text surfaces.
        for path, content in binary:
            current = path.read_bytes() if path.exists() else None
            if current != content:
                drift.append(path.relative_to(root).as_posix())
                if not check:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
        # Orphan-clean stale version-numbered download zips. A prior-version aiqt-skill-<old>.zip left in
        # site/downloads after a version bump is a stale shipped surface, so it is removed on a normal run
        # and reported as drift on --check (exit 1). The stable alias aiqt-skill.zip (no version segment, so
        # it does not match the aiqt-skill-*.zip shape) and the CURRENT ZIP_VERSIONED_PARTS copy are kept.
        # The scan fails closed on an unreadable downloads dir (os.walk onerror=raise), mirroring the
        # reserved-subtree orphan scan below, so an I/O error can never conceal a stale zip. Top level only:
        # version-numbered zips live directly under site/downloads (the reserved aiqt/ subtree is scanned
        # separately below).
        downloads_dir = root.joinpath(*ZIP_PARTS[:-1])
        current_versioned = ZIP_VERSIONED_PARTS[-1]
        if dir_present(downloads_dir):
            for dirpath, _dirs, filenames in os.walk(downloads_dir, onerror=_raise):
                _dirs[:] = []  # top level only: do not descend into the reserved aiqt/ subtree
                for fn in sorted(filenames):
                    if fn.startswith("aiqt-skill-") and fn.endswith(".zip") and fn != current_versioned:
                        stale = Path(dirpath) / fn
                        drift.append("orphan " + stale.relative_to(root).as_posix())
                        if not check:
                            stale.unlink()
        for name, content in sorted(reserved_map.items()):
            target = reserved_dir / name
            current = target.read_text(encoding="utf-8") if target.exists() else None
            if current != content:
                drift.append((reserved_dir / name).relative_to(root).as_posix())
                if not check:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
        # Orphan scan over the reserved subtree ONLY (it is 100% generated). A generated file with no
        # backing output (a stale SKILL.md, a leftover references/*.md) is an orphan and is removed.
        if dir_present(reserved_dir):
            for dirpath, _dirs, filenames in os.walk(reserved_dir, onerror=_raise):
                for fn in sorted(filenames):
                    f = Path(dirpath) / fn
                    rel = f.relative_to(reserved_dir).as_posix()
                    if rel not in reserved_map:
                        drift.append("orphan " + (reserved_dir / rel).relative_to(root).as_posix())
                        if not check:
                            f.unlink()
    except (OSError, UnicodeError) as exc:
        # UnicodeError (UnicodeDecodeError) covers the generated-TARGET reads above (the standalone text
        # output and the reserved-subtree targets): a non-UTF-8 target decodes as UTF-8 there, so a
        # corrupt target fails closed (exit 2) rather than a raw traceback, the same OSError path (a
        # read-only fs, a permission error, a full disk) already fails closed on. The binary zip target
        # reconciles on bytes (read_bytes), so it is untouched by this widening.
        print("error: {}".format(exc))
        return 2
    if check and drift:
        print("drift: " + "; ".join(drift))
        print("run tools/gen_skill.py to regenerate")
        return 1
    return 0


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test_main()
    return run_gen(repo_root(), "--check" in argv)


# --- self-test ------------------------------------------------------------------------------------
# Proves the gate fails on the things it must catch, against synthetic temp trees, never the real tree:
#   1. a well-formed source renders and round-trips (regenerate, then --check is clean),
#   2. a source citing an unknown corpus-id fails closed (exit 2), the anti-fabrication gate,
#   3. a hand-edited (drifted) SKILL.md makes --check report drift (exit 1),
#   4. an orphan file in the reserved output subtree is detected (exit 1),
#   5. an invalid-UTF-8 reserved target fails closed (exit 2), not a raw UnicodeDecodeError traceback:
#      guards the widened (OSError, UnicodeError) reconcile arm (F-154),
#   6. a stale version-numbered download zip (aiqt-skill-0.0.0.zip) is flagged as drift on --check
#      (exit 1) and removed on a normal regen, while the alias and the current versioned copy are kept.

_APEX = """---
corpus-id: prjint1
origin: pack
family: aiqt
apex: true
slug: project-integrity
---
# The AIQT principle

(Accuracy = Integrity = Quality = Trust) > Progress > Speed > Cost. The self-test apex body.
"""

_SEC = """---
corpus-id: secunt
origin: pack
family: security
facet: SECI
slug: untrusted-content
---
# Untrusted content is data

A self-test security rule body.
"""

_SEC2 = """---
corpus-id: secres
origin: pack
family: security
facet: SECA
slug: resource-bounds
---
# Bounded consumption

A second self-test security rule body.
"""

_CONDUCT1 = """---
corpus-id: nofabr
origin: pack
family: aiqt
tier: 10
facet: ACCUR
slug: no-fabrication
---
# No fabrication

A self-test conduct rule body.
"""

_CONDUCT2 = """---
corpus-id: exetgt
origin: pack
family: aiqt
tier: 10
facet: QUALI
slug: confirm-execution-target
---
# Confirm the execution target

A second self-test conduct rule body.
"""

_SKILL_SRC = """=== meta ===
name: aiqt
version: __ZIPVER__
license: CC-BY-SA-4.0
date: 2026-01-01
apex-id: prjint1

=== description ===
A self-test skill description line.

=== instructions-preamble ===
HOW TO USE THIS FILE
Self-test preamble.

=== body-aiqt ===
Self-test AIQT body.

=== body-rules ===
Self-test rules body.

=== conduct-intro ===
Self-test conduct intro.

=== conduct-unconditional ===
[nofabr]
**Self-test conduct unconditional entry.** Body text.

=== conduct-conditional ===
[exetgt]
**Self-test conduct conditional entry.** Body text.

=== security-intro ===
Self-test security intro.

=== security-unconditional ===
[secunt]
**Self-test unconditional entry.** Body text.

=== security-conditional ===
[secres]
**Self-test conditional entry.** Body text.

=== security-capability-note ===
Self-test capability note.
"""


def _write_fixture(root, skill_src_text):
    src = root.joinpath(*CORPUS_PARTS)
    (src / "aiqt").mkdir(parents=True)
    (src / "security").mkdir(parents=True)
    (src / "aiqt" / "00-project-integrity.md").write_text(_APEX, encoding="utf-8")
    (src / "security" / "untrusted-content.md").write_text(_SEC, encoding="utf-8")
    (src / "security" / "resource-bounds.md").write_text(_SEC2, encoding="utf-8")
    (src / "aiqt" / "no-fabrication.md").write_text(_CONDUCT1, encoding="utf-8")
    (src / "aiqt" / "confirm-execution-target.md").write_text(_CONDUCT2, encoding="utf-8")
    (root.joinpath(*SKILL_SRC_PARTS)).parent.mkdir(parents=True)
    (root.joinpath(*SKILL_SRC_PARTS)).write_text(skill_src_text, encoding="utf-8")
    manifest = root.joinpath(*IDENTITY_MANIFEST_PARTS)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('[plugin]\nauthor-name = "Self Test Operator"\nhomepage = "https://example.test"\n',
                        encoding="utf-8")


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout

    # good_src resolves cleanly (apex plus two distinct security ids). Its version is pinned to the shipped
    # ZIP_VERSIONED_PARTS literal so the build-time version-match assertion passes for a well-formed fixture;
    # a leg below pins a MISMATCHED version to prove the assertion fires. bad_src cites a corpus-id that is
    # not in the fixture corpus, so the anti-fabrication gate must fire.
    good_src = _SKILL_SRC.replace("__ZIPVER__", zip_versioned_version())
    bad_src = good_src.replace("[secres]\n", "[nosuch9]\n")

    def capture(root, check):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_gen(root, check)
        return code, buf.getvalue()

    failures = []
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-skill-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    try:
        # 1. Well-formed source renders and round-trips clean.
        good = tmp / "good"
        good.mkdir()
        _write_fixture(good, good_src)
        code, out = capture(good, False)
        if code != 0:
            failures.append("well-formed generate expected exit 0, got {}\n{}".format(code, out))
        code, out = capture(good, True)
        if code != 0:
            failures.append("well-formed --check after generate expected exit 0 (clean), got {}\n{}".format(code, out))

        # 1b. The rendered header carries the five-line identity block: four two-space CommonMark hard
        # breaks (Version/Author/Website/GitHub) and NONE on the Licence line. Removing any break (so the
        # header would collapse in a sanitizing viewer) fails here even after regeneration.
        good_md = good.joinpath(*RESERVED_PARTS) / "SKILL.md"
        hdr_lines = [ln for ln in good_md.read_text(encoding="utf-8").splitlines()
                     if ln.split(":", 1)[0] in ("Version", "Author", "Website", "GitHub", "Licence")]

        def _two(ln):
            return ln != ln.rstrip() and ln[len(ln.rstrip()):] == "  "
        breaks = sum(1 for ln in hdr_lines if _two(ln))
        if breaks != 4:
            failures.append("header expected exactly four two-space hard breaks, got {}".format(breaks))
        if any(ln.startswith("Licence:") and _two(ln) for ln in hdr_lines):
            failures.append("the Licence header line must not carry a two-space hard break")

        # 2. Unknown corpus-id fails closed (exit 2): the anti-fabrication gate.
        badid = tmp / "badid"
        badid.mkdir()
        _write_fixture(badid, bad_src)
        code, out = capture(badid, False)
        if code != 2:
            failures.append("unknown-corpus-id source expected exit 2, got {}\n{}".format(code, out))

        # 3. A hand-edited (drifted) SKILL.md makes --check report drift (exit 1).
        drifted = tmp / "drifted"
        drifted.mkdir()
        _write_fixture(drifted, good_src)
        capture(drifted, False)  # generate a clean tree first
        skill_md = drifted.joinpath(*RESERVED_PARTS) / "SKILL.md"
        skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
        code, out = capture(drifted, True)
        if code != 1:
            failures.append("drifted SKILL.md expected --check exit 1, got {}\n{}".format(code, out))

        # 4. An orphan file in the reserved output subtree is detected (exit 1).
        orphan = tmp / "orphan"
        orphan.mkdir()
        _write_fixture(orphan, good_src)
        capture(orphan, False)
        stray = orphan.joinpath(*RESERVED_PARTS) / "references"
        stray.mkdir(parents=True)
        (stray / "stale.md").write_text("# orphan\n", encoding="utf-8")
        code, out = capture(orphan, True)
        if code != 1 or "orphan" not in out:
            failures.append("orphan output expected --check exit 1 naming the orphan, got {}\n{}".format(code, out))

        # 5. An invalid-UTF-8 reserved target fails closed (exit 2), not a raw UnicodeDecodeError
        #    traceback. run_gen reads each standalone/reserved target as UTF-8, so a non-UTF-8 target
        #    must be caught by the widened (OSError, UnicodeError) arm. A revert to the narrow
        #    OSError-only arm makes this RAISE instead of returning 2, so the case guards the widening.
        badenc = tmp / "badenc"
        badenc.mkdir()
        _write_fixture(badenc, good_src)
        capture(badenc, False)  # generate a clean tree first
        skill_md = badenc.joinpath(*RESERVED_PARTS) / "SKILL.md"
        skill_md.write_bytes(b"\xff\xfe not utf-8")
        try:
            with redirect_stdout(io.StringIO()):
                code = run_gen(badenc, True)
        except Exception as exc:  # a reverted narrow arm raises UnicodeDecodeError here
            code = "raised {}".format(type(exc).__name__)
        if code != 2:
            failures.append("invalid-UTF-8 reserved target expected exit 2 (fail-closed), got {}".format(code))

        # 6. A rule placed in a facet-inappropriate block fails closed (exit 2): the section-facet guard.
        #    Swap the conduct rule (nofabr, ACCUR) with the security rule (secunt, SECI) so each lands in
        #    the wrong block; resolve_group must reject the mismatched facet. Removing the guard makes this
        #    case FAIL rather than pass: with the pre-sort facet validation gone, the sort key raises on the
        #    mismatched facet, so the self-test still fails when the guard is absent (the case guards the guard).
        misfacet = tmp / "misfacet"
        misfacet.mkdir()
        swapped = (good_src.replace("[nofabr]", "[__tmpswap__]")
                           .replace("[secunt]", "[nofabr]")
                           .replace("[__tmpswap__]", "[secunt]"))
        _write_fixture(misfacet, swapped)
        code, out = capture(misfacet, False)
        if code != 2:
            failures.append("facet-misplaced rule expected exit 2 (section-facet guard), got {}\n{}".format(code, out))

        # 7. A skill meta version that does NOT match the shipped ZIP_VERSIONED_PARTS literal fails closed
        #    (exit 2): the version-match assertion. This is the case a skill bump that forgets to update
        #    ZIP_VERSIONED_PARTS would hit. Removing the assertion makes this leg fail.
        mismatch = tmp / "mismatch"
        mismatch.mkdir()
        mm_src = _SKILL_SRC.replace("__ZIPVER__", zip_versioned_version() + "-unbumped")
        _write_fixture(mismatch, mm_src)
        code, out = capture(mismatch, False)
        if code != 2:
            failures.append("skill version mismatched to the versioned-zip literal expected exit 2, "
                            "got {}\n{}".format(code, out))

        # 8. A stale version-numbered download zip (aiqt-skill-0.0.0.zip) beside the current one is reported
        #    as drift on --check (exit 1) and removed on a normal regen, while the stable alias and the
        #    current versioned copy are kept. Start from a clean tree, drop a stale zip, then check and
        #    regenerate. Removing the orphan-clean scan makes this leg fail (the stale zip survives).
        stalezip = tmp / "stalezip"
        stalezip.mkdir()
        _write_fixture(stalezip, good_src)
        capture(stalezip, False)  # generate a clean tree first
        downloads = stalezip.joinpath(*ZIP_PARTS[:-1])
        stale = downloads / "aiqt-skill-0.0.0.zip"
        stale.write_bytes(b"PK\x03\x04 stale prior-version zip bytes")
        code, out = capture(stalezip, True)
        if code != 1 or "orphan" not in out or "aiqt-skill-0.0.0.zip" not in out:
            failures.append("stale versioned zip expected --check exit 1 naming the orphan, got {}\n{}".format(
                code, out))
        code, out = capture(stalezip, False)  # regen removes it
        if code != 0 or stale.exists():
            failures.append("regen expected to remove the stale versioned zip, got exit {} (present={})\n{}"
                            .format(code, stale.exists(), out))
        # The alias and the current versioned copy are kept, and --check is clean again.
        alias = stalezip.joinpath(*ZIP_PARTS)
        current = stalezip.joinpath(*ZIP_VERSIONED_PARTS)
        if not alias.exists() or not current.exists():
            failures.append("orphan-clean must keep the alias and the current versioned zip (alias={}, "
                            "current={})".format(alias.exists(), current.exists()))
        code, out = capture(stalezip, True)
        if code != 0:
            failures.append("after removing the stale zip, --check expected exit 0 (clean), got {}\n{}".format(
                code, out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: well-formed source round-trips clean (SKILL.md and the zips); an unknown "
          "corpus-id, an invalid-UTF-8 target, and a version/zip-literal mismatch each fail closed (exit 2); "
          "a drifted SKILL.md, an orphan reserved output, and a stale version-numbered download zip are "
          "caught (exit 1, the stale zip removed on regen while the alias and current copy are kept); a "
          "facet-misplaced rule fails closed (exit 2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
