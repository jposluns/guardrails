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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from _standards import dir_present  # noqa: E402
from gen_rules import load_corpus  # noqa: E402

# The canonical apex ordering. It MUST appear verbatim in the apex rule body (prjint1), so the skill's
# top-line ordering is tied to the corpus and cannot silently drift from it.
CANON_ORDERING = "(Accuracy = Integrity = Quality = Trust) > Progress > Speed > Cost"
# The security family's CIA-plus-privacy order, matching gen_agents.CIA_FACET_ORDER, so the rendered
# security entries sort deterministically the same way the rest of the pack orders that family.
CIA_FACET_ORDER = {"SECC": 0, "SECI": 1, "SECA": 2, "SECP": 3}

# Output locations, as relative parts joined under the repo root (or a --self-test temp root).
RESERVED_PARTS = ("site", "downloads", "aiqt")            # 100% generated: orphan-scanned in full
INSTRUCTIONS_PARTS = ("site", "downloads", "aiqt-instructions.txt")  # a standalone named output
ZIP_PARTS = ("site", "downloads", "aiqt-skill.zip")       # a standalone named BINARY output
SKILL_SRC_PARTS = (".aiqt", "core", "skill", "skill-source.md")
CORPUS_PARTS = (".aiqt", "core", "rules")

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py; VER-CORE 6.5).
RENDERER_DECL = {"renderer-id": "skill", "semantics-revision": 1}
GENSRC_OUTPUTS = (
    {"target": "site/downloads/aiqt/", "kind": "tree",
     "sources": (".aiqt/core/skill/skill-source.md", ".aiqt/core/rules/"),
     "regenerate": "python3 tools/gen_skill.py"},
    {"target": "site/downloads/aiqt-instructions.txt", "kind": "file",
     "sources": (".aiqt/core/skill/skill-source.md", ".aiqt/core/rules/"),
     "regenerate": "python3 tools/gen_skill.py"},
    {"target": "site/downloads/aiqt-skill.zip", "kind": "file",
     "sources": (".aiqt/core/skill/skill-source.md", ".aiqt/core/rules/"),
     "regenerate": "python3 tools/gen_skill.py"},
)

# The public download is packed deterministically so its bytes never depend on the wall clock, the host
# OS, or the zlib version: a fixed ZIP-epoch timestamp, members in sorted order, a fixed unix mode, and
# STORED (uncompressed) entries. STORED also keeps the member text in the clear, so the leak gates read
# the real SKILL.md rather than an opaque compressed blob, and --check can byte-compare the archive.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
ZIP_MEMBER = "aiqt/SKILL.md"

_SECTION = re.compile(r"^=== (\S+) ===$")
_ENTRY = re.compile(r"^\[([a-z0-9]{6,})\]$")
_META = re.compile(r"^([a-z0-9-]+):\s*(.*)$")

REQUIRED_SECTIONS = ("meta", "description", "instructions-preamble", "body-aiqt", "body-rules",
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

    def resolve_group(entries, group):
        out = []
        for cid, text in entries:
            if cid not in by_id:
                raise ValueError("skill source cites unknown corpus-id '{}'".format(cid))
            if cid in included:
                raise ValueError("skill source cites corpus-id '{}' more than once".format(cid))
            included.append(cid)
            fm = by_id[cid][0]
            out.append((cid, text, fm.get("facet", ""), str(fm.get("slug", ""))))
        # Deterministic CIA-facet order (SECC, SECI, SECA, SECP), then slug, matching gen_agents.
        out.sort(key=lambda e: (CIA_FACET_ORDER.get(e[2], 9), e[3]))
        return [text for _cid, text, _f, _s in out]

    uncond_texts = resolve_group(source["unconditional"], "unconditional")
    cond_texts = resolve_group(source["conditional"], "conditional")

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
        "uncond_texts": uncond_texts,
        "cond_texts": cond_texts,
        "included_ids": sorted(included),
        "corpus_hash": "sha256:" + digest.hexdigest(),
    }


def _frontmatter(data):
    desc = "\n".join("  " + line for line in data["description"].splitlines())
    return ("---\n"
            "name: {name}\n"
            "description: >-\n{desc}\n"
            "license: {license}\n"
            "version: {version}\n"
            "---").format(name=data["meta"]["name"], desc=desc,
                          license=data["meta"]["license"], version=data["meta"]["version"])


def _security_block(data):
    blocks = ["# Security", data["security_intro"]]
    blocks += data["uncond_texts"]
    blocks.append("## If your platform exposes tools, browsing, retrieval, or persistent memory")
    blocks += data["cond_texts"]
    blocks.append(data["capability_note"])
    return "\n\n".join(blocks)


def render_skill(data):
    blocks = [
        _frontmatter(data),
        "# AIQT\n\n" + data["body_aiqt"],
        "# Rules\n\n" + data["body_rules"],
        _security_block(data),
    ]
    return "\n\n".join(blocks) + "\n"


def render_zip(data):
    """The public aiqt-skill.zip, built in memory from the SAME SKILL.md the reserved subtree carries, so
    the zip's aiqt/SKILL.md member is byte-identical to the tracked SKILL.md. Deterministic (see ZIP_EPOCH
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
    header = ("AIQT: a standard for your AI assistant\n"
              "Version {v} . Licensed under CC BY-SA 4.0 "
              "(https://creativecommons.org/licenses/by-sa/4.0/)").format(v=data["meta"]["version"])
    blocks = [
        header,
        data["preamble"],
        "=" * 60,
        "# AIQT\n\n" + data["body_aiqt"],
        "# Rules\n\n" + data["body_rules"],
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
        "generator-version": "1",
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
        "The published aiqt-skill.zip is packed deterministically from this same SKILL.md by the same "
        "generator, so its aiqt/SKILL.md member is byte-identical to the text recorded here.",
    ]
    return "\n".join(lines) + "\n"


def build_outputs(root):
    """Load the corpus and skill source under root and render every output. Returns
    (reserved_map, standalone, binary): reserved_map is {filename: text} for the reserved
    site/downloads/aiqt/ subtree, standalone is [(abs_path, text)] for named text outputs beside it, and
    binary is [(abs_path, bytes)] for named binary outputs beside it (the deterministic download zip).
    Raises ValueError/OSError (an unknown id, a malformed or unreadable source): the caller fails closed.
    Parameterized on root so the conformance suite and the self-test can call it off the real tree."""
    corpus = load_corpus(root.joinpath(*CORPUS_PARTS))
    source = parse_source(root.joinpath(*SKILL_SRC_PARTS))
    data = resolve(source, corpus)
    reserved_map = {
        "SKILL.md": render_skill(data),
        "manifest.json": render_manifest(data),
        "provenance.md": render_provenance(data),
    }
    standalone = [(root.joinpath(*INSTRUCTIONS_PARTS), render_instructions(data))]
    binary = [(root.joinpath(*ZIP_PARTS), render_zip(data))]
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
#      guards the widened (OSError, UnicodeError) reconcile arm (F-154).

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

_SKILL_SRC = """=== meta ===
name: aiqt
version: 9.9.9
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
    (root.joinpath(*SKILL_SRC_PARTS)).parent.mkdir(parents=True)
    (root.joinpath(*SKILL_SRC_PARTS)).write_text(skill_src_text, encoding="utf-8")


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout

    # good_src resolves cleanly (apex plus two distinct security ids). bad_src cites a corpus-id that is
    # not in the fixture corpus, so the anti-fabrication gate must fire.
    good_src = _SKILL_SRC
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: well-formed source round-trips clean; an unknown corpus-id and an "
          "invalid-UTF-8 target fail closed (exit 2); a drifted SKILL.md and an orphan output are "
          "caught (exit 1).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
