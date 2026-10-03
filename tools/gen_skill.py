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
  site/downloads/aiqt-skill-1.0.6.zip the version-numbered copy the site links to (byte-identical alias)

Latest only, allow-listed (D-SKILL-LATEST-ONLY): only the current skill version is served, and the top
level of site/downloads admits ONLY declared outputs: this generator's (aiqt-instructions.txt,
aiqt-skill.zip, the current aiqt-skill-<version>.zip), the generated aiqt/ directory, and the top-level
outputs of the two sibling owners, read from each owner's own table (gen_mappings.GENSRC_OUTPUTS:
mappings.csv and mappings.json; check_sized_instructions.SIZED: the three sized condensations). Any OTHER
entry, whatever its name, case or type (a stale versioned zip, a backup, a case or separator variant, a
symlink, directory or special file), fails closed, naming the entry for the maintainer to remove with git rm
(a normal run exits 2 before writing anything; --check exits 1). The generator never deletes a file
ANYWHERE: an orphan in the reserved site/downloads/aiqt/ tree is likewise named for git rm (normal run
exit 2, --check exit 1), never unlinked. Every output is read and written relative to directory
descriptors: each parent component is opened with O_DIRECTORY|O_NOFOLLOW from the repository root down, the
temporary file is created O_EXCL|O_NOFOLLOW at that descriptor, fsynced, and renamed over the target with
os.replace anchored to the same descriptor, so a symlink at any component, or a parent directory swapped
mid-run, cannot redirect a read or write outside the repository (each operation binds to the held
descriptor, not to a re-resolved path).

  gen_skill.py            regenerate every output
  gen_skill.py --check    fail (exit 1) on drift; exit 2 on a malformed source or an unknown corpus-id
  gen_skill.py --self-test  prove the gate fails on drift, an unknown id, an orphan output, a bad target
"""
import errno
import hashlib
import io
import json
import os
import re
import stat
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
ZIP_VERSIONED_PARTS = ("site", "downloads", "aiqt-skill-1.0.6.zip")  # the version-numbered copy the site
# links to. The literal version here is tied to the skill meta version (skill-source.md) by a fail-closed
# assertion in build_outputs, so a skill bump that forgets to update this name fails closed.
# Skill version policy: the skill is versioned on its own today (skill 1.0.6 under pack release 1.0.5). From
# the 1.1.1 pack release on, the skill version equals the pack version; the check that mirrors the two lands
# with the 1.1.1 cut, not here.
SKILL_SRC_PARTS = (".aiqt", "core", "skill", "skill-source.md")
CORPUS_PARTS = (".aiqt", "core", "rules")
# The single canonical operator-identity source (the same file the hooks generator and the portability
# gate read from). The public attribution line (GD-56) is built from the [plugin] author-name here plus
# the pack's public source URL, so the maintainer's name is never a literal in any scanned source file;
# it enters the two download artefacts only, where the portability gate carries its narrow exemption.
IDENTITY_MANIFEST_PARTS = (".aiqt", "core", "hooks", "manifest.toml")
ATTRIBUTION_SOURCE_URL = "https://github.com/jposluns/guardrails"
# The hand-authored evidence page source names the skill version served from the install page in one
# sentence; --check ties that sentence to the declared skill version (via the build-time assertion, the
# ZIP_VERSIONED_PARTS literal equals the skill meta version whenever a build succeeds), so a skill bump
# that forgets the evidence page is caught as drift. The page itself stays hand-authored (gen_site renders
# it to site/evidence.html); this gate verifies, it never writes there.
EVIDENCE_PARTS = ("docs", "evidence.md")
_EVIDENCE_SKILL_SENTENCE = re.compile(r"served from the install page is (\d+\.\d+\.\d+)")

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py; VER-CORE 6.5).
RENDERER_DECL = {"renderer-id": "skill", "semantics-revision": 2}
# GENSRC_OUTPUTS is STATICALLY parsed by gen_gensrc.py and must be a LITERAL (a tuple of dict literals),
# so each source list is inlined rather than shared through a name. The hooks manifest is a content-bearing
# source: the public attribution line (GD-56) is rendered from its [plugin] author-name, so a change to that
# name changes these outputs and must re-trigger regeneration. LICENSE is likewise a content-bearing source
# of the two download zips: render_zip packs it as a member, so a LICENSE edit changes those archives.
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
                 ".aiqt/core/hooks/manifest.toml", "LICENSE"),
     "regenerate": "python3 tools/gen_skill.py"},
    {"target": "site/downloads/aiqt-skill-1.0.6.zip", "kind": "file",
     "sources": (".aiqt/core/skill/skill-source.md", ".aiqt/core/rules/",
                 ".aiqt/core/hooks/manifest.toml", "LICENSE"),
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
LICENSE_MEMBER = "LICENSE"        # the canonical Apache License 2.0, packed alongside SKILL.md so a
                                  # recipient of the download alone also gets the licence terms (the
                                  # licence's redistribution terms). Read from the pack root at build time (fail-closed).
LICENSE_PARTS = ("LICENSE",)      # LICENSE lives at the pack root, beside README/NOTICE

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
            "Licence: Apache License 2.0 (https://www.apache.org/licenses/LICENSE-2.0)").format(
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
        # pack's Apache License 2.0. The portability gate carries a narrow, reviewed exemption for exactly this line.
        "---\n\n" + data["attribution"],
    ]
    return "\n\n".join(blocks) + "\n"


def versioned_zip_basename(version):
    """The version-numbered download filename for a skill version, e.g. 'aiqt-skill-1.0.6.zip'. This is the
    shared SHAPE helper: it spells the filename PATTERN in one place, so the build-time match assertion, the
    install-page block (gen_install.py), and any other caller derive the name the same way. It is NOT the
    single source of the concrete versioned name: that name is spelled as a literal in several spots (the
    four in RELEASING.md's bump checklist: the skill-source.md meta version, the ZIP_VERSIONED_PARTS literal
    and its GENSRC_OUTPUTS target, check_portability.BINARY_ALLOW, and ownership.toml [checkout].binary).
    Those literals are kept consistent by the fail-closed version-match assertion in build_outputs plus the
    bump checklist, not by true single-sourcing."""
    return "aiqt-skill-{}.zip".format(version)


# A versioned skill zip is exactly 'aiqt-skill-<MAJOR>.<MINOR>.<PATCH>.zip' (strict, case-sensitive).
_VERSIONED_ZIP = re.compile(r"^aiqt-skill-[0-9]+\.[0-9]+\.[0-9]+\.zip$")


def _sibling_downloads_outputs():
    """Basenames of the top-level site/downloads entries owned by the OTHER tools, read from each owner's
    own declared output table, never from a name pattern: gen_mappings.GENSRC_OUTPUTS (mappings.csv,
    mappings.json) and check_sized_instructions.SIZED (the three hand-authored sized condensations).
    Imported lazily so the module import graph stays acyclic at load time (check_sized_instructions itself
    imports gen_skill for its parsers; by the time a scan runs, this module is fully loaded)."""
    import gen_mappings           # noqa: PLC0415  lazy by design, see docstring
    import check_sized_instructions  # noqa: PLC0415  lazy by design, see docstring
    prefix = "site/downloads/"
    names = []
    for out in gen_mappings.GENSRC_OUTPUTS:
        target = out["target"]
        if target.startswith(prefix) and "/" not in target[len(prefix):]:
            names.append(target[len(prefix):])
    for rel, _cap in check_sized_instructions.SIZED:
        if rel.startswith(prefix) and "/" not in rel[len(prefix):]:
            names.append(rel[len(prefix):])
    return names


def is_versioned_zip(filename):
    """True for a version-numbered skill zip basename, strictly 'aiqt-skill-<MAJOR>.<MINOR>.<PATCH>.zip'.
    The stable alias aiqt-skill.zip has no version segment, so it does not match. A backup, a 'v' prefix, a
    case variant or a suffix (aiqt-skill-backup.zip, AIQT-skill-1.0.5.zip, aiqt-skill-1.0.5.ZIP) does not
    match either; latest_only_problems refuses those as unrecognised skill entries."""
    return _VERSIONED_ZIP.match(filename) is not None


def _is_regular(path):
    """lstat path (never following a link): None when absent, True for a regular file, False for a symlink,
    a directory or a special file. Other OSErrors propagate so an unreadable entry fails closed."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    return stat.S_ISREG(st.st_mode)


def versioned_zips(downloads_dir):
    """The sorted version-numbered skill zip basenames directly under downloads_dir that are REGULAR files by
    lstat (top level only; the reserved aiqt/ subtree is not a download location). An absent dir has none; an
    unreadable one raises OSError, which run_gen surfaces as exit 2."""
    if not dir_present(downloads_dir):
        return []
    return sorted(fn for fn in os.listdir(downloads_dir)
                  if is_versioned_zip(fn) and _is_regular(os.path.join(downloads_dir, fn)))


def latest_only_problems(downloads_dir, current_versioned, require_current):
    """Latest-only plus ALLOW-LIST (D-SKILL-LATEST-ONLY) scan of the top level of downloads_dir, by lstat,
    never following a link. The only entries permitted are this generator's declared outputs (the
    instructions file, the alias aiqt-skill.zip, and current_versioned), each a regular file, the sibling
    owners' declared top-level outputs (_sibling_downloads_outputs, read from their own tables), and the
    reserved aiqt/ directory, which must be a real directory by lstat (anything else at that name raises
    OSError: fail-closed exit 2 in both modes, since every generated read and write runs under it). Every
    OTHER entry, whatever its name, type or case, is a problem, never skipped and never deleted: a stale
    versioned zip, an undeclared name (a backup, a case or separator variant, an unrelated file), or a
    symlink, directory or special file. require_current adds a missing current versioned zip as a problem
    (--check; a normal run writes it). Returns a list of messages, empty when clean; an unreadable dir
    raises OSError (exit 2)."""
    problems = []
    if dir_present(downloads_dir):
        alias = ZIP_PARTS[-1]
        reserved_name = RESERVED_PARTS[-1]
        allowed = {INSTRUCTIONS_PARTS[-1], alias, current_versioned}
        allowed.update(_sibling_downloads_outputs())
        for fn in sorted(os.listdir(downloads_dir)):
            full = os.path.join(downloads_dir, fn)
            if fn == reserved_name:
                try:
                    st = os.lstat(full)
                except FileNotFoundError:  # vanished between listdir and lstat: nothing there to judge
                    continue
                if not stat.S_ISDIR(st.st_mode):
                    raise OSError("refusing site/downloads/{}: it is a symlink or not a directory, so the "
                                  "generated tree under it is not written or read through (remove it with "
                                  "git rm and rerun)".format(fn))
                continue
            regular = _is_regular(full)
            if regular is None:  # vanished between listdir and lstat: nothing there to judge
                continue
            if not regular:
                problems.append("{} is a symlink, directory or special file, not a regular file (refused; "
                                "remove it with git rm site/downloads/{})".format(fn, fn))
            elif fn in allowed:
                continue
            elif is_versioned_zip(fn):
                problems.append("{} is a stale versioned zip; only {} is served (remove it with git rm "
                                "site/downloads/{})".format(fn, current_versioned, fn))
            else:
                problems.append("{} is not an allowed entry; only the declared outputs ({}) and the "
                                "generated {}/ directory may sit here (remove it with git rm "
                                "site/downloads/{})".format(fn, ", ".join(sorted(allowed)),
                                                            reserved_name, fn))
    if require_current and _is_regular(os.path.join(str(downloads_dir), current_versioned)) is None:
        problems.append("the current versioned zip {} is missing (found 0; run tools/gen_skill.py)".format(
            current_versioned))
    return problems


def _refuse_link_path(root, path, want_dir=False):
    """Fail closed (OSError) if path, or any directory between root and path, is a symlink or not the
    expected kind (a directory for a parent, a regular file for the target, or a directory when want_dir),
    checked by lstat. Absent components are fine (a normal run creates them as real directories). This is a
    path-based REPORTING guard for the read-only orphan walk (which never deletes or writes); enforcement
    for every output read and write is the descriptor walk in _open_dir_fd, which has no check-to-use
    gap."""
    rel = Path(path).relative_to(root)
    cur = Path(root)
    for i, part in enumerate(rel.parts):
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return
        last = i == len(rel.parts) - 1 and not want_dir
        if not (stat.S_ISREG(st.st_mode) if last else stat.S_ISDIR(st.st_mode)):
            raise OSError("refusing {}: {} is a symlink or not a {}, so it is not written or read through "
                          "(remove it with git rm and rerun)".format(
                              rel.as_posix(), cur.relative_to(root).as_posix(),
                              "regular file" if last else "directory"))


def _refusal(rel_posix, comp_posix, kind):
    """The shared fail-closed refusal for a component that is a symlink or not the expected kind."""
    return OSError("refusing {}: {} is a symlink or not a {}, so it is not written or read through "
                   "(remove it with git rm and rerun)".format(rel_posix, comp_posix, kind))


def _open_dir_fd(root, rel_parts, target_rel, create=False):
    """Open the directory root/<rel_parts> and return its file descriptor, walking ONE COMPONENT AT A TIME
    with os.open(O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=<previous component's fd>). Every component is
    bound at open time: a symlink or non-directory anywhere on the walk is refused by the kernel
    (ELOOP/ENOTDIR, surfaced as the shared refusal OSError), and once a component's descriptor is held, a
    concurrent rename or symlink swap of that component cannot redirect later operations, because they run
    relative to the descriptor, not a re-resolved path. The repository root itself is opened by path once,
    without O_NOFOLLOW (a checkout legitimately reachable through a symlinked ancestor, e.g. /var/tmp, is
    the trusted anchor). With create=True an absent component is created (os.mkdir with dir_fd, then
    reopened with O_NOFOLLOW, so a swap between mkdir and open is still refused); with create=False an
    absent component returns None. The caller must os.close the returned fd. target_rel is the output's
    repo-relative posix path, used only in the refusal message."""
    fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, part in enumerate(rel_parts):
            comp = "/".join(rel_parts[:i + 1])
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    os.close(fd)
                    return None
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass  # raced into existence; the O_NOFOLLOW reopen below still refuses a link
                try:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise _refusal(target_rel, comp, "directory") from exc
                    raise
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise _refusal(target_rel, comp, "directory") from exc
                raise
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_output(root, path, binary):
    """The current content of a generated output, or None when absent. Descriptor-anchored: the parent is
    opened by _open_dir_fd and the target with O_NOFOLLOW relative to it, then fstat-verified a regular
    file, so a symlink or non-regular file at the path, or a symlink anywhere on the parent walk, is
    refused with OSError (exit 2), never read through (O_NONBLOCK keeps a FIFO at the name from blocking
    the open; it is then refused by the fstat check)."""
    rel = Path(path).relative_to(root)
    parent_fd = _open_dir_fd(root, rel.parts[:-1], rel.as_posix())
    if parent_fd is None:
        return None
    try:
        try:
            fd = os.open(rel.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise _refusal(rel.as_posix(), rel.as_posix(), "regular file") from exc
            raise
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise _refusal(rel.as_posix(), rel.as_posix(), "regular file")
            data = fh.read()
    finally:
        os.close(parent_fd)
    return data if binary else data.decode("utf-8")


def _write_output(root, path, content):
    """Write a generated output anchored to its parent directory's descriptor, never a re-resolved path:
    _open_dir_fd walks (and with create, makes) every parent component under O_NOFOLLOW; a symlink or
    non-regular file already at the target name is refused (lstat relative to the held descriptor); the
    temporary file is created with O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW at that descriptor, written, flushed
    and fsynced; then os.replace(src_dir_fd=..., dst_dir_fd=...) renames it over the target within the SAME
    held directory, so a parent swapped between any two steps cannot redirect the write outside the
    repository (a rename replaces the directory entry and never writes through a link; the narrow residual,
    a regular file swapped for a symlink at the final name after the lstat, loses only that symlink entry
    to the rename and still writes nothing outside). The mode follows the umask like a fresh write; text is
    encoded UTF-8 with newlines untouched, byte-identical to the previous writer on POSIX."""
    rel = Path(path).relative_to(root)
    parent_fd = _open_dir_fd(root, rel.parts[:-1], rel.as_posix(), create=True)
    try:
        name = rel.parts[-1]
        try:
            st = os.lstat(name, dir_fd=parent_fd)
        except FileNotFoundError:
            st = None
        if st is not None and not stat.S_ISREG(st.st_mode):
            raise _refusal(rel.as_posix(), rel.as_posix(), "regular file")
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        tfd = None
        for _ in range(64):  # O_EXCL retry on a (user-planted) name collision; never reuses a file
            tmp = ".{}.{}.tmp".format(name, os.urandom(4).hex())
            try:
                tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666,
                              dir_fd=parent_fd)
                break
            except FileExistsError:
                continue
        if tfd is None:
            raise OSError("could not create a temporary file beside {}".format(rel.as_posix()))
        try:
            with os.fdopen(tfd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=parent_fd)  # this run's own temporary file only, never a user file
            except OSError:
                pass
            raise
    finally:
        os.close(parent_fd)


def zip_versioned_version():
    """The version substring embedded in the ZIP_VERSIONED_PARTS basename ('aiqt-skill-<v>.zip'). Lets the
    self-test and conformance fixtures pin their skill meta version to the shipped literal, so the
    version-match assertion in build_outputs passes for a well-formed fixture and fires only on a mismatch."""
    base = ZIP_VERSIONED_PARTS[-1]
    return base[len("aiqt-skill-"):-len(".zip")]


def render_zip(data, license_text):
    """The public aiqt-skill.zip, built in memory from the SAME SKILL.md the reserved subtree carries, so
    the zip's SKILL.md member is byte-identical to the tracked SKILL.md. It also carries the canonical
    LICENSE (Apache License 2.0) so a recipient of the download alone also receives the licence terms,
    as the licence's redistribution terms require. Deterministic (see ZIP_EPOCH note): fixed timestamp, sorted
    members, STORED compression, fixed unix mode, no wall-clock, so two runs produce identical bytes and
    --check can byte-compare the archive."""
    members = {
        ZIP_MEMBER: render_skill(data).encode("utf-8"),
        LICENSE_MEMBER: license_text.encode("utf-8"),
    }
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
              "Version {v} . Licensed under the Apache License 2.0 "
              "(https://www.apache.org/licenses/LICENSE-2.0)\n"
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
        "- Licence: Apache License 2.0 (https://www.apache.org/licenses/LICENSE-2.0)",
        "- Date: {}".format(m["date"]),
        "- Source corpus hash: {}".format(data["corpus_hash"]),
        "- Included rules (by corpus id): {}".format(", ".join(data["included_ids"])),
        "",
        "The published aiqt-skill.zip and its version-numbered copy aiqt-skill-{}.zip are "
        "byte-identical, packed deterministically by the same generator: each carries this same SKILL.md "
        "(byte-identical to the text recorded here) and the canonical LICENSE (Apache License 2.0), so a "
        "recipient of the download alone also receives the licence terms.".format(m["version"]),
    ]
    return "\n".join(lines) + "\n"


def plugin_identity(root):
    """Read the operator NAME and HOMEPAGE from the [plugin] table of the canonical identity manifest, so
    neither is ever a literal in a scanned source file. Returns (name, homepage). An absent, unparseable,
    name-less, or homepage-less manifest is fail-closed (OSError/ValueError, which build_outputs surfaces
    as exit 2)."""
    path = root.joinpath(*IDENTITY_MANIFEST_PARTS)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except RecursionError as exc:
        # tomllib raises RecursionError (a RuntimeError, not a ValueError) on a deeply nested array or inline
        # table; map it into the ValueError family build_outputs surfaces as exit 2
        # (F-TOML-BARE-VALUEERROR-CLASS).
        raise ValueError("{}: TOML nesting is too deep to parse ({})".format(path, exc)) from exc
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
    return "AIQT Guardrails by {}, {}, Apache License 2.0".format(name, ATTRIBUTION_SOURCE_URL)


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
    # The canonical LICENSE is packed into the archive (fail-closed: a missing/unreadable LICENSE raises
    # OSError, which the caller surfaces as exit 2) so the download alone carries the Apache License 2.0.
    license_text = root.joinpath(*LICENSE_PARTS).read_text(encoding="utf-8")
    zip_bytes = render_zip(data, license_text)
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
        # An absent corpus is a transition state (desired empty): the scan below then names every surviving
        # output as an orphan for git rm, never concealed and never deleted. A PRESENT corpus with a
        # missing/unreadable skill source is malformed (the
        # OSError from parse_source propagates here as exit 2), which is the correct fail-closed outcome.
        if dir_present(corpus_dir):
            reserved_map, standalone, binary = build_outputs(root)
        else:
            reserved_map, standalone, binary = {}, [], []
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2
    try:
        # Latest-only plus allow-list (D-SKILL-LATEST-ONLY), run FIRST, before any write: exactly one
        # version-numbered skill zip is served, named for the skill meta version (build_outputs has already
        # asserted ZIP_VERSIONED_PARTS spells it), beside the alias, and the ONLY other top-level entries
        # permitted are the declared sibling outputs and the reserved aiqt/ directory. The generator never
        # deletes here: an undeclared entry, whatever its name, type or case, is named for
        # the maintainer to git rm. On --check that (or a missing current copy) is drift (exit 1); a normal
        # run fails closed (exit 2) before touching any output. An extra entry is not an output, so the byte
        # comparison below never sees it: this scan is the only detection for it.
        downloads_dir = root.joinpath(*ZIP_PARTS[:-1])
        problems = latest_only_problems(downloads_dir, ZIP_VERSIONED_PARTS[-1], check and bool(binary))
        if problems:
            msg = ("latest-only: expected exactly one versioned skill zip under site/downloads, {} (the "
                   "declared skill version), beside {} and the other declared outputs: {}".format(
                       ZIP_VERSIONED_PARTS[-1], ZIP_PARTS[-1], "; ".join(problems)))
            if not check:
                print("error: " + msg)
                return 2
            # --check reports this alone (exit 1) before reading any output, so a link at an output name
            # is named here rather than surfacing as the read refusal below.
            print("drift: " + msg)
            print("remove each named entry with git rm, then run tools/gen_skill.py")
            return 1
        for path, content in standalone:
            current = _read_output(root, path, False)
            if current != content:
                drift.append(path.relative_to(root).as_posix())
                if not check:
                    _write_output(root, path, content)
        # Named binary outputs (the download zip) reconcile on bytes, so a stale or hand-swapped archive
        # is caught by the same drift gate as the text surfaces.
        for path, content in binary:
            current = _read_output(root, path, True)
            if current != content:
                drift.append(path.relative_to(root).as_posix())
                if not check:
                    _write_output(root, path, content)
        for name, content in sorted(reserved_map.items()):
            target = reserved_dir / name
            current = _read_output(root, target, False)
            if current != content:
                drift.append((reserved_dir / name).relative_to(root).as_posix())
                if not check:
                    _write_output(root, target, content)
        # Orphan scan over the reserved subtree ONLY (it is 100% generated). An entry with no backing
        # output (a stale SKILL.md, a leftover references/ directory or file, ANY subdirectory or symlink:
        # the generated tree is flat) is an orphan. The generator NEVER deletes it: a normal run fails
        # closed (exit 2) and --check reports drift (exit 1), each naming the entry and the git rm to run.
        # The reserved dir itself (and every parent) must be a real directory by lstat: os.walk follows a
        # symlinked top, so a link here would make the scan name entries living outside the repository
        # (the scan only reads names; every read and write above is descriptor-anchored).
        _refuse_link_path(root, reserved_dir, want_dir=True)
        orphans = []
        if dir_present(reserved_dir):
            for dirpath, dirs, filenames in os.walk(reserved_dir, onerror=_raise):
                for fn in sorted(dirs) + sorted(filenames):
                    rel = (Path(dirpath) / fn).relative_to(reserved_dir).as_posix()
                    if rel not in reserved_map:
                        orp = (reserved_dir / rel).relative_to(root).as_posix()
                        orphans.append("orphan {} (not a generated output; remove it with git rm {})".format(
                            orp, orp))
        if orphans:
            if not check:
                print("error: the generator never deletes a file; " + "; ".join(orphans))
                return 2
            drift.extend(orphans)
        # Evidence currency (--check only; the page is hand-authored, so a normal run cannot fix it): the
        # docs/evidence.md install-page sentence must name the declared skill version. Skipped when the
        # page is absent (fixture trees) or there is no build (absent-corpus transition); an unreadable or
        # non-UTF-8 page fails closed through the arm below.
        evidence_problem = None
        if check and binary:
            ev = root.joinpath(*EVIDENCE_PARTS)
            if os.path.lexists(ev):
                m = _EVIDENCE_SKILL_SENTENCE.search(ev.read_text(encoding="utf-8"))
                want = zip_versioned_version()
                if m is None or m.group(1) != want:
                    evidence_problem = ("docs/evidence.md must say the chat skill 'served from the install "
                                        "page is {}' (the declared skill version), found {}; edit "
                                        "docs/evidence.md".format(
                                            want, "'...is {}'".format(m.group(1)) if m else "no such sentence"))
    except (OSError, UnicodeError) as exc:
        # UnicodeError (UnicodeDecodeError) covers the generated-TARGET reads above (the standalone text
        # output and the reserved-subtree targets): a non-UTF-8 target decodes as UTF-8 there, so a
        # corrupt target fails closed (exit 2) rather than a raw traceback, the same OSError path (a
        # read-only fs, a permission error, a full disk) already fails closed on. The binary zip target
        # reconciles on bytes (read_bytes), so it is untouched by this widening.
        print("error: {}".format(exc))
        return 2
    if check and (drift or evidence_problem):
        if drift:
            print("drift: " + "; ".join(drift))
            print("run tools/gen_skill.py to regenerate")
        if evidence_problem:
            print("drift: " + evidence_problem)
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
#   6. a stale versioned zip or ANY undeclared top-level entry (the allow-list) is never deleted: a normal
#      run fails closed (exit 2, writing nothing) and --check exits 1, each naming the entry for git rm,
#   7. the scan refuses by lstat a symlink, directory or dangling link at a skill zip name (including the
#      current one) and names a missing current versioned zip (exit 1),
#   8. no read or write goes through a symlinked output, a symlinked reserved directory, or a symlinked
#      PARENT directory (exit 2, the outside file unchanged), and a parent directory swapped at the write
#      boundary (both reviews' race reproduction) still cannot redirect a write outside the tree: the held
#      descriptor pins the real directory,
#   9. an orphan in the reserved tree is named for git rm and never deleted; a hard-linked output is never
#      written through; a stale or missing evidence install-page sentence is --check drift.

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
license: Apache-2.0
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
    # build_outputs packs the pack-root LICENSE into the zip (fail-closed if absent); a well-formed
    # fixture therefore ships one so regeneration succeeds.
    (root / "LICENSE").write_text("Apache License 2.0\n\n(self-test fixture licence text)\n",
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
        # 0. F-TOML-BARE-VALUEERROR-CLASS: a 1200-deep nested array in the identity manifest makes tomllib
        #    raise RecursionError (a RuntimeError, not a ValueError); plugin_identity must map it into the
        #    ValueError family run_gen surfaces as exit 2. The recursion limit is pinned to the CPython
        #    default 1000 (test-hermeticity) and restored in finally.
        deep = tmp / "deep-identity"
        deep.joinpath(*IDENTITY_MANIFEST_PARTS[:-1]).mkdir(parents=True)
        deep.joinpath(*IDENTITY_MANIFEST_PARTS).write_text("[plugin]\ndeep = " + "[" * 1200 + "]" * 1200 + "\n",
                                                           encoding="utf-8")
        prev_reclimit = sys.getrecursionlimit()
        sys.setrecursionlimit(1000)
        try:
            plugin_identity(deep)
            failures.append("a deeply nested identity manifest must fail closed (ValueError -> exit 2)")
        except ValueError:
            pass
        except RecursionError:
            failures.append("a deeply nested identity manifest let a bare RecursionError escape plugin_identity")
        finally:
            sys.setrecursionlimit(prev_reclimit)

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

        # 4. An orphan entry in the reserved output subtree is never deleted: --check exits 1 and a normal
        #    run exits 2, each naming the entry and the git rm to run, and the entry survives both. Red if
        #    the scan deletes again (the old unlink) or is dropped (nothing else sees an orphan).
        orphan = tmp / "orphan"
        orphan.mkdir()
        _write_fixture(orphan, good_src)
        capture(orphan, False)
        stray = orphan.joinpath(*RESERVED_PARTS) / "references"
        stray.mkdir(parents=True)
        (stray / "stale.md").write_text("# orphan\n", encoding="utf-8")
        code, out = capture(orphan, True)
        if code != 1 or "orphan" not in out or "git rm" not in out:
            failures.append("orphan output expected --check exit 1 naming the orphan and git rm, "
                            "got {}\n{}".format(code, out))
        code, out = capture(orphan, False)
        if (code != 2 or "orphan" not in out or "git rm" not in out
                or not (stray / "stale.md").exists()):
            failures.append("orphan output expected a normal run to exit 2 with the orphan kept (never "
                            "deleted), got {} (kept={})\n{}".format(
                                code, (stray / "stale.md").exists(), out))

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

        # 8. The generator never deletes a file under site/downloads, and the top level is ALLOW-LISTED:
        #    a stale versioned zip, any aiqt-skill variant (case, separator, prefix, suffix, 'v', 'latest',
        #    a hidden or unicode-dash name) and ANY other undeclared entry, whatever its name, fails closed:
        #    a normal run exits 2 BEFORE writing anything (a drifted
        #    instructions file stays drifted), --check exits 1, each names the entry and git rm, and the
        #    entry is kept byte for byte. Removing the allow-list scan makes this leg fail (nothing else
        #    sees an extra entry); removing its normal-run exit-2 path makes the exit-2 assertion fail;
        #    reverting to the old aiqt[-_]skill name-pattern scan fails on the last six names.
        foreign = ("aiqt-skill-0.0.0.zip", "aiqt-skill-backup.zip", "aiqt-skill-notes.zip",
                   "AIQT-skill-1.0.5.zip", "aiqt-skill-1.0.5.ZIP", "aiqt_skill-1.0.5.zip",
                   "aiqt-skill-latest.zip", "aiqt-skill-v1.0.5.zip", "aiqt-skill-1.0.5.zip.bak",
                   ".aiqt-skill-1.0.5.zip", "aiqtskill-1.0.5.zip", "skill-aiqt-1.0.5.zip",
                   "aiqt skill-1.0.5.zip", "aiqt\u2010skill-1.0.5.zip", "user-notes.txt")
        for i, fn in enumerate(foreign):
            fx = tmp / "foreign{}".format(i)
            fx.mkdir()
            _write_fixture(fx, good_src)
            capture(fx, False)  # generate a clean tree first
            entry = fx.joinpath(*ZIP_PARTS[:-1]) / fn
            entry.write_bytes(b"USER OWNED - NOT GENERATED")
            instr = fx.joinpath(*INSTRUCTIONS_PARTS)
            instr.write_text("local edit\n", encoding="utf-8")
            code, out = capture(fx, False)
            if (code != 2 or "latest-only" not in out or fn not in out or "git rm" not in out
                    or entry.read_bytes() != b"USER OWNED - NOT GENERATED"):
                failures.append("{} must make a normal run fail closed (exit 2, named, git rm, kept), got {} "
                                "(kept={})\n{}".format(fn, code, entry.exists(), out))
            if instr.read_text(encoding="utf-8") != "local edit\n":
                failures.append("{}: a normal run that fails latest-only must write nothing".format(fn))
            code, out = capture(fx, True)
            if code != 1 or "latest-only" not in out or fn not in out or not entry.exists():
                failures.append("{} expected --check exit 1 naming it, got {}\n{}".format(fn, code, out))
            # The strict MAJOR.MINOR.PATCH pattern: only aiqt-skill-0.0.0.zip reads as a stale versioned zip;
            # every other name is an undeclared entry. A loosened pattern misreads these.
            kind = "stale versioned zip" if i == 0 else "not an allowed entry"
            if kind not in out or is_versioned_zip(fn) != (i == 0):
                failures.append("{} expected to be classed as {!r}, got\n{}".format(fn, kind, out))

        # 9. Latest-only (D-SKILL-LATEST-ONLY) scans by lstat and refuses (never skips) a non-regular entry
        #    at a skill zip name; it also names a missing current copy. Each case is red if the scan reverts
        #    to os.path.isfile (a link to a file passes, a directory is skipped) or drops its lstat filter.
        latest = tmp / "latest"
        latest.mkdir()
        _write_fixture(latest, good_src)
        capture(latest, False)
        ldl = latest.joinpath(*ZIP_PARTS[:-1])
        found = versioned_zips(ldl)
        if found != [ZIP_VERSIONED_PARTS[-1]]:
            failures.append("a clean tree must hold exactly the current versioned zip, got {}".format(found))
        alias_bytes = latest.joinpath(*ZIP_PARTS).read_bytes()
        cases = (
            ("directory", "aiqt-skill-0.0.1.zip", lambda p: p.mkdir()),
            ("symlink to a directory", "aiqt-skill-0.0.1.zip",
             lambda p: os.symlink(str(latest.joinpath(*RESERVED_PARTS)), str(p))),
            ("dangling symlink", "aiqt-skill-0.0.1.zip", lambda p: os.symlink(str(tmp / "nowhere"), str(p))),
            ("symlink to a regular zip", "aiqt-skill-0.0.1.zip",
             lambda p: os.symlink(str(latest.joinpath(*ZIP_PARTS)), str(p))),
            ("case-variant directory", "AIQT-Skill-0.0.1.ZIP", lambda p: p.mkdir()),
        )
        for label, fn, make in cases:
            entry = ldl / fn
            make(entry)
            for chk, want in ((True, 1), (False, 2)):
                code, out = capture(latest, chk)
                if code != want or "not a regular file" not in out or fn not in out:
                    failures.append("a {} at {} expected exit {} (not a regular file), got {}\n{}".format(
                        label, fn, want, code, out))
            if not os.path.lexists(entry):
                failures.append("a {} at {} must be kept, never deleted".format(label, fn))
            (entry.rmdir if (entry.is_dir() and not entry.is_symlink()) else entry.unlink)()
        # The current versioned zip replaced by a symlink to the alias: the scan names it (exit 1 on --check,
        # not the exit-2 read refusal a scan without lstat would fall through to); a normal run refuses.
        cur = latest.joinpath(*ZIP_VERSIONED_PARTS)
        cur.unlink()
        os.symlink(ZIP_PARTS[-1], str(cur))
        code, out = capture(latest, True)
        if code != 1 or "not a regular file" not in out or ZIP_VERSIONED_PARTS[-1] not in out:
            failures.append("a symlinked current versioned zip expected --check exit 1 (not a regular file), "
                            "got {}\n{}".format(code, out))
        code, out = capture(latest, False)
        if code != 2 or not cur.is_symlink() or latest.joinpath(*ZIP_PARTS).read_bytes() != alias_bytes:
            failures.append("a symlinked current versioned zip expected a normal run to refuse (exit 2, link "
                            "and alias untouched), got {}\n{}".format(code, out))
        cur.unlink()
        code, out = capture(latest, True)
        if code != 1 or "latest-only" not in out or "found 0" not in out:
            failures.append("a missing current versioned zip expected latest-only drift (exit 1, found 0), "
                            "got {}\n{}".format(code, out))
        code, out = capture(latest, False)
        if code != 0 or versioned_zips(ldl) != [ZIP_VERSIONED_PARTS[-1]]:
            failures.append("regen expected to restore exactly the current versioned zip, got {}\n{}".format(
                code, out))
        code, out = capture(latest, True)
        if code != 0:
            failures.append("after restoring the current zip, --check expected exit 0, got {}\n{}".format(
                code, out))
        if any(n.endswith(".tmp") for n in os.listdir(ldl)):
            failures.append("a normal run left a temporary file behind: {}".format(sorted(os.listdir(ldl))))

        # 10. No write goes through a link. A symlink at a generated output (the instructions file) or a
        #     symlinked reserved directory, each pointing at a file or directory OUTSIDE the tree, is refused
        #     (exit 2 in both modes) and the outside content is unchanged. Reverting _write_output to
        #     Path.write_text, or dropping the lstat walk, overwrites the outside file and fails here.
        outside = tmp / "outside"
        outside.mkdir()
        victim = outside / "victim.txt"
        victim.write_bytes(b"precious user data")
        wl = tmp / "writelink"
        wl.mkdir()
        _write_fixture(wl, good_src)
        capture(wl, False)
        instr = wl.joinpath(*INSTRUCTIONS_PARTS)
        instr.unlink()
        os.symlink(str(victim), str(instr))
        # The allow-list scan sees the link first (a non-regular entry at a declared name): --check drift
        # (exit 1), a normal run fails closed (exit 2) before writing anything, outside content unchanged.
        for chk, want in ((True, 1), (False, 2)):
            code, out = capture(wl, chk)
            if (code != want or "not a regular file" not in out
                    or victim.read_bytes() != b"precious user data"):
                failures.append("a symlinked output expected exit {} naming it (not a regular file) with "
                                "the outside file unchanged (check={}), got {}\n{}".format(
                                    want, chk, code, out))
        # The writer itself refuses a link (not only the read before it), and leaves no temporary file.
        try:
            _write_output(wl, instr, "overwrite attempt\n")
            failures.append("_write_output must refuse a symlinked target (OSError)")
        except OSError:
            pass
        if victim.read_bytes() != b"precious user data" or not instr.is_symlink():
            failures.append("_write_output wrote through a symlink to a file outside the tree")
        instr.unlink()
        capture(wl, False)
        # The outside directory holds byte-exact copies of the generated reserved files plus a user file, so
        # no reserved write is due and only the reserved-dir lstat guard stops the orphan removal from
        # deleting the user file through the link.
        rdir = wl.joinpath(*RESERVED_PARTS)
        outdir = tmp / "outside-reserved"
        shutil.copytree(str(rdir), str(outdir))
        user_file = outdir / "user-notes.txt"
        user_file.write_bytes(b"precious user data")
        shutil.rmtree(rdir)
        os.symlink(str(outdir), str(rdir))
        for chk in (True, False):
            code, out = capture(wl, chk)
            if code != 2 or "refusing" not in out or user_file.read_bytes() != b"precious user data":
                failures.append("a symlinked reserved directory expected exit 2 with the outside tree unchanged "
                                "(check={}), got {} (user file kept={})\n{}".format(
                                    chk, code, user_file.exists(), out))
        # With the corpus absent there are no outputs to read or write, so the reserved-dir guard alone stops
        # the orphan removal from following the link and deleting every outside file.
        shutil.rmtree(wl.joinpath(*CORPUS_PARTS))
        code, out = capture(wl, False)
        if code != 2 or "refusing" not in out or not user_file.exists():
            failures.append("an absent corpus with a symlinked reserved directory expected exit 2 with the "
                            "outside tree unchanged, got {} (user file kept={})\n{}".format(
                                code, user_file.exists(), out))

        # 11. A symlinked PARENT directory: site/downloads replaced by a symlink to an outside directory
        #     that itself looks like a plausible downloads tree (so the allow-list scan, which lists
        #     through the path, stays clean) with a drifted instructions file. The descriptor walk opens
        #     every component with O_NOFOLLOW, so both modes refuse (exit 2) and the outside victim is
        #     unchanged. Red if the walk binds only the final component or follows a symlinked parent,
        #     or if reads and writes revert to re-resolved paths.
        pswap = tmp / "parentswap"
        pswap.mkdir()
        _write_fixture(pswap, good_src)
        capture(pswap, False)
        dl = pswap.joinpath(*ZIP_PARTS[:-1])
        out_dl = tmp / "outside-downloads"
        shutil.copytree(str(dl), str(out_dl))
        pvictim = out_dl / INSTRUCTIONS_PARTS[-1]
        pvictim.write_bytes(b"precious outside")
        os.rename(str(dl), str(dl) + ".real")
        os.symlink(str(out_dl), str(dl))
        for chk in (True, False):
            code, out = capture(pswap, chk)
            if code != 2 or "refusing" not in out or pvictim.read_bytes() != b"precious outside":
                failures.append("a symlinked site/downloads parent expected exit 2 refusing with the "
                                "outside file unchanged (check={}), got {}\n{}".format(chk, code, out))
        os.unlink(str(dl))
        os.rename(str(dl) + ".real", str(dl))

        # 12. Both reviews' RACE reproduction, pinned at two boundaries. (a) tempfile.mkstemp is patched to
        #     swap site/downloads for a symlink to an outside directory the moment a temporary file is
        #     requested: the shipped writer never asks for a path-resolved temporary file, so the swap never
        #     fires and the regen succeeds; a writer that re-resolves the path at its write boundary (the
        #     raced lstat-then-mkstemp-then-os.replace shape) trips the swap, writes the outside victim and
        #     goes red. (b) _open_dir_fd is wrapped to perform the SAME swap right after the parent
        #     descriptor is handed back: the held descriptor pins the real directory, so the write lands
        #     there and the outside victim stays unchanged even though the path now points outside.
        race = tmp / "race"
        race.mkdir()
        _write_fixture(race, good_src)
        capture(race, False)
        rdl = race.joinpath(*ZIP_PARTS[:-1])
        rout = tmp / "outside-race"
        rout.mkdir()
        rvictim = rout / INSTRUCTIONS_PARTS[-1]
        rvictim.write_bytes(b"precious outside")
        swap_state = dict(done=False)

        def _swap_once():
            if not swap_state["done"] and not os.path.islink(str(rdl)):
                os.rename(str(rdl), str(rdl) + ".real")
                os.symlink(str(rout), str(rdl))
                swap_state["done"] = True

        instr_r = race.joinpath(*INSTRUCTIONS_PARTS)
        instr_r.write_text("stale local edit\n", encoding="utf-8")  # a write is due
        real_mkstemp = tempfile.mkstemp

        def _swapping_mkstemp(*args, **kwargs):
            _swap_once()
            return real_mkstemp(*args, **kwargs)

        tempfile.mkstemp = _swapping_mkstemp
        try:
            code, out = capture(race, False)
        finally:
            tempfile.mkstemp = real_mkstemp
        if rvictim.read_bytes() != b"precious outside":
            failures.append("the mkstemp-boundary parent swap reached the outside victim: a write "
                            "re-resolved a path at its write boundary\n{}".format(out))
        if swap_state["done"]:  # only a path-resolving writer requests a temporary file by path
            os.unlink(str(rdl))
            os.rename(str(rdl) + ".real", str(rdl))
        elif code != 0 or instr_r.read_text(encoding="utf-8") == "stale local edit\n":
            failures.append("the race fixture regen expected exit 0 with the drifted output rewritten, "
                            "got {}\n{}".format(code, out))

        swap_state["done"] = False
        instr_r.write_text("stale local edit\n", encoding="utf-8")
        real_walk = _open_dir_fd

        def _swapping_walk(root_, rel_parts, target_rel, create=False):
            fd = real_walk(root_, rel_parts, target_rel, create=create)
            _swap_once()
            return fd

        globals()["_open_dir_fd"] = _swapping_walk
        try:
            _write_output(race, instr_r, "generated replacement\n")
        finally:
            globals()["_open_dir_fd"] = real_walk
        moved = Path(str(rdl) + ".real") if swap_state["done"] else rdl
        if rvictim.read_bytes() != b"precious outside":
            failures.append("the descriptor-boundary parent swap reached the outside victim")
        if (moved / INSTRUCTIONS_PARTS[-1]).read_text(encoding="utf-8") != "generated replacement\n":
            failures.append("a write with the parent swapped after the descriptor walk must land in the "
                            "pinned real directory, not follow the new path")
        if swap_state["done"]:
            os.unlink(str(rdl))
            os.rename(str(moved), str(rdl))

        # 13. A hard link at an output name is never written THROUGH: the writer renames a fresh inode over
        #     the directory entry, so the outside name keeps its bytes (and drops to one link) while the
        #     output is regenerated. Red for any writer that opens the existing output in place (truncating
        #     the shared inode), even one that keeps every pre-check.
        hard = tmp / "hardlink"
        hard.mkdir()
        _write_fixture(hard, good_src)
        capture(hard, False)
        instr_h = hard.joinpath(*INSTRUCTIONS_PARTS)
        fresh = instr_h.read_text(encoding="utf-8")
        outside_h = tmp / "outside-hardlink.txt"
        os.link(str(instr_h), str(outside_h))
        instr_h.write_text("stale local edit\n", encoding="utf-8")  # updates BOTH names (one inode)
        code, out = capture(hard, False)
        if code != 0 or outside_h.read_text(encoding="utf-8") != "stale local edit\n":
            failures.append("regenerating over a hard-linked output must not write through the shared "
                            "inode, got {} (outside={})\n{}".format(
                                code, outside_h.read_text(encoding="utf-8")[:40], out))
        if instr_h.read_text(encoding="utf-8") != fresh or os.lstat(str(instr_h)).st_nlink != 1:
            failures.append("regenerating over a hard-linked output must replace the directory entry with "
                            "a fresh single-link inode")

        # 14. The evidence currency gate: docs/evidence.md must name the declared skill version in its
        #     install-page sentence. A stale version or a missing sentence is --check drift (exit 1) naming
        #     docs/evidence.md; the current version is clean. Red if the gate is removed.
        evid = tmp / "evidence"
        evid.mkdir()
        _write_fixture(evid, good_src)
        capture(evid, False)
        ev_md = evid / "docs" / "evidence.md"
        ev_md.parent.mkdir()
        ev_md.write_text("The chat skill now served from the install page is 0.0.1, under the Apache "
                         "License 2.0.\n", encoding="utf-8")
        code, out = capture(evid, True)
        if code != 1 or "docs/evidence.md" not in out or zip_versioned_version() not in out:
            failures.append("a stale evidence skill version expected --check exit 1 naming docs/evidence.md "
                            "and the declared version, got {}\n{}".format(code, out))
        ev_md.write_text("An unrelated page with no such sentence.\n", encoding="utf-8")
        code, out = capture(evid, True)
        if code != 1 or "docs/evidence.md" not in out:
            failures.append("a missing evidence sentence expected --check exit 1, got {}\n{}".format(
                code, out))
        ev_md.write_text("The chat skill now served from the install page is {}, under the Apache "
                         "License 2.0.\n".format(zip_versioned_version()), encoding="utf-8")
        code, out = capture(evid, True)
        if code != 0:
            failures.append("a current evidence skill version expected --check exit 0, got {}\n{}".format(
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
          "a drifted SKILL.md is caught (exit 1); nothing is ever deleted: an orphan reserved entry and a "
          "stale versioned zip plus fourteen other undeclared top-level entries (the allow-list) are kept "
          "and named for git rm (normal run exit 2 writing nothing, --check exit 1); the scan refuses by "
          "lstat a directory, a symlink and a symlinked current zip and names a missing current zip; a "
          "symlinked output, reserved directory or PARENT directory is refused with the outside file "
          "unchanged (exit 2); a parent swapped at the write boundary (the raced swap) and a hard-linked "
          "output are never written through (the descriptor pins the directory; the rename replaces the "
          "entry); a stale evidence install-page sentence is --check drift; a facet-misplaced rule fails "
          "closed (exit 2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
