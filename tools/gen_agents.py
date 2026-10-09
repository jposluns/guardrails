#!/usr/bin/env python3
"""Generate AGENTS.md (the Codex adapter) from the .aiqt/core/rules/ sources.

Same core that feeds Claude's .claude/rules/ tree, so a Codex session gets identical AIQT governance. The
rules are concatenated in AIQT priority order (apex; then Accuracy, Integrity, Quality, Trust at tier 10;
then Progress, Speed, Cost; then the security family in CIA-plus-privacy order), each rule's title demoted
to a section heading. The layout (legacy, or composed with reviewed blocks around the rules) comes from the
block registry .aiqt/core/adapter-blocks.toml through tools/_adapter_compose.py, the engine this generator
shares with gen_adapters.py. --check drift-gates AGENTS.md byte for byte; exit 2 on a malformed source or
registry, or on a write that would erase a hand edit.

  gen_agents.py              regenerate AGENTS.md
  gen_agents.py --check      exit 1 if AGENTS.md differs from a fresh composition; write nothing
  gen_agents.py --self-test  synthetic trees for the composition engine's fail-closed matrix
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: gen_agents.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from _adapter_compose import legacy_render, run  # noqa: E402
from gen_rules import load_corpus  # noqa: E402

AIQT_FACET_ORDER = {"ACCUR": 0, "INTEG": 1, "QUALI": 2, "TRUST": 3}
CIA_FACET_ORDER = {"SECC": 0, "SECI": 1, "SECA": 2, "SECP": 3}

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py; VER-CORE 6.5).
RENDERER_DECL = {"renderer-id": "agents", "semantics-revision": 1}
GENSRC_OUTPUTS = (
    {"target": "AGENTS.md", "kind": "file",
     "sources": (".aiqt/core/rules/", ".aiqt/core/adapter-blocks.toml"),
     "regenerate": "python3 tools/gen_agents.py"},
)

AGENTS_REL = "AGENTS.md"
HEADER = ("# AGENTS.md",
          "",
          "AIQT Guardrails governance for a Codex session. GENERATED from the same rule corpus that",
          "feeds Claude's .claude/rules/ tree (tools/gen_agents.py); do not hand-edit. Rules are in AIQT",
          "priority order: the apex, then Accuracy, Integrity, Quality, Trust, then Progress, Speed,",
          "Cost, then the security family.",
          "")


def sort_key(fm):
    if fm["family"] == "aiqt":
        if fm.get("apex") is True:
            return (0, 0, 0, str(fm["slug"]))
        return (1, int(fm["tier"]), AIQT_FACET_ORDER.get(fm.get("facet", ""), 9), str(fm["slug"]))
    return (2, 0, CIA_FACET_ORDER.get(fm.get("facet", ""), 9), str(fm["slug"]))


def body_of(path):
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    body = text[end + 5:].strip()
    if body.startswith("# "):        # demote the rule title to a section heading under the AGENTS.md H1
        body = "#" + body
    return body


def ordered_bodies(corpus):
    """The rule bodies of a loaded corpus ([(path, frontmatter, rel)]) in AIQT priority order."""
    pairs = sorted(((src, fm) for src, fm, _ in corpus), key=lambda pf: sort_key(pf[1]))
    return [body_of(src) for src, _fm in pairs]


def corpus_bodies(src_dir):
    """Load the corpus under src_dir and return its ordered rule bodies (run()'s corpus reader)."""
    return ordered_bodies(load_corpus(src_dir))


def render(pairs):
    """The legacy AGENTS.md text for sorted (path, frontmatter) pairs; kept for existing importers."""
    return legacy_render(HEADER, [body_of(p) for p, _ in pairs])


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run(repo_root(), "--check" in args, [(AGENTS_REL, HEADER)], "tools/gen_agents.py", corpus_bodies)


# --- self-test ------------------------------------------------------------------------------------
# Synthetic temp trees only, never the real tree. Each vector drives _adapter_compose.run (the engine both
# generators use) against its own tree, and every write-mode refusal also asserts that the target bytes
# are unchanged. Vectors, each asserted on its exact exit:
#   T1  legacy layout: the bytes equal the pre-registry join; --check 0; a rewrite changes nothing.
#   T2  composed layout: exact golden bytes (one before-rules and one after-rules block); idempotent.
#   T3-T5  a hand edit inside an OPF block, inside AIQT-RULES, or in the header: --check 1, write 2.
#   T6  text outside any block: --check 1, write 2.
#   T7  a duplicated pair, a missing END, an orphan END, a nested BEGIN, an END naming another block, a
#       missing AIQT-RULES block, and malformed markers (lower case, short digest, no "(generated",
#       leading space): --check 1, write 2.
#   T8  an undeclared block id: write 2; the same id once retired: the block is dropped, write 0.
#   T9  blocks out of registry order, digests intact: --check 1, write 0 and reorders.
#   T10 a legacy target holding a pasted marker line: --check 1, write 2.
#   T11 migration: the exact legacy rendering composes (write 0); with extra text, write 2.
#   T12 each registry error: exit 2 in both modes, nothing written.
#   T13 each block-source error: exit 2, nothing written.
#   T14 a CRLF-converted legacy AGENTS.md: --check 1.
#   T15 two targets in one run (the gen_adapters pair) with one refused: the other is not written.
#   T16 no rule corpus: a composed target exits 2 and is kept; a legacy target is drift (--check 1) and
#       is deleted (write 0).
#   T17 a rule corpus path that exists but cannot be stat'ed (a symlink loop): exit 2, nothing deleted.
#   T18 a rule body holding a marker-like line, composed target: exit 2.

_APEX = ("---\ncorpus-id: prjint1\norigin: pack\nfamily: aiqt\napex: true\nslug: project-integrity\n---\n"
         "\n# Project integrity\n\nApex text.\n")
_RULE = ("---\ncorpus-id: ruleaa\norigin: pack\nfamily: aiqt\ntier: 10\nfacet: ACCUR\nslug: fixture-rule\n"
         "---\n\n# Fixture rule\n\nRule text.\n")
_BODIES = ["## Project integrity\n\nApex text.", "## Fixture rule\n\nRule text."]
_RULES_INNER = "\n\n".join(_BODIES)
_A_INNER = "alpha line"
_Z_INNER = "zulu line one\n\nzulu line two"
# (id, target, position, order, source rel, source text)
_BLOCK_A = ("OPF-A", "AGENTS.md", "before-rules", 1, "opf/blocks/a.md", _A_INNER + "\n")
_BLOCK_Z = ("OPF-Z", "AGENTS.md", "after-rules", 1, "opf/blocks/z.md", _Z_INNER + "\n")


def self_test_main():
    import hashlib
    import io
    import os
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout
    import _adapter_compose as ac

    agents_only = [(AGENTS_REL, HEADER)]
    # The two gen_adapters targets with stand-in headers: T15 and T17 test run()'s handling of several
    # targets in one call, which gen_adapters.main() makes with its own headers.
    adapter_pair = [("GEMINI.md", ("# GEMINI.md", "", "Stand-in header.", "")),
                    (".github/copilot-instructions.md", ("# Copilot", "", "Stand-in header.", ""))]
    prefix = "\n".join(HEADER) + "\n"

    def seg(name, inner):
        return ("<!-- " + name + ":BEGIN (generated sha256=" + hashlib.sha256(inner.encode("utf-8")).hexdigest()
                + ") -->\n" + inner + "\n<!-- " + name + ":END -->")

    seg_a, seg_rules, seg_z = seg("OPF-A", _A_INNER), seg("AIQT-RULES", _RULES_INNER), seg("OPF-Z", _Z_INNER)
    golden = (prefix + seg_a + "\n\n" + seg_rules + "\n\n" + seg_z + "\n").encode("utf-8")
    legacy_golden = (prefix + _RULES_INNER + "\n").encode("utf-8")
    composed_reg = ac.registry_text([(AGENTS_REL, "composed")], [_BLOCK_A, _BLOCK_Z])

    failures = []
    counter = [0]

    def read(p):
        try:
            return p.read_bytes()
        except FileNotFoundError:
            return None

    def tree(registry, blocks=(_BLOCK_A, _BLOCK_Z), rules=(_APEX, _RULE), agents=None):
        counter[0] += 1
        root = tmp / "t{}".format(counter[0])
        rdir = root / ".aiqt" / "core" / "rules"
        rdir.mkdir(parents=True)
        for i, text in enumerate(rules):
            (rdir / "r{}.md".format(i)).write_text(text, encoding="utf-8")
        if registry is not None:
            (root / ".aiqt" / "core" / "adapter-blocks.toml").write_text(registry, encoding="utf-8")
        for _bid, _t, _p, _o, source, text in blocks:
            sp = root / source
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        if agents is not None:
            (root / AGENTS_REL).write_bytes(agents)
        return root

    def drive(root, check, targets=None):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = run(root, check, targets or agents_only, "tools/gen_agents.py", corpus_bodies)
        return code, buf.getvalue()

    def expect(label, root, check, want, targets=None, unchanged=True):
        rels = [rel for rel, _h in (targets or agents_only)]
        before = [read(root / rel) for rel in rels]
        code, out = drive(root, check, targets)
        if code != want:
            failures.append("{}: {} expected exit {}, got {}: {}".format(
                label, "--check" if check else "write", want, code, out.strip()))
        if unchanged and [read(root / rel) for rel in rels] != before:
            failures.append("{}: {} changed a target it must leave alone".format(
                label, "--check" if check else "write"))
        return out

    def refused(label, root, check_code=1):
        expect(label, root, True, check_code)
        expect(label, root, False, 2)

    def mutated(label, data, old, new):
        if old not in data:
            failures.append("{}: fixture mutation did not apply".format(label))
        return data.replace(old, new, 1)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-agents-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    try:
        # T1. Legacy layout: the pre-registry join, byte for byte, and stable.
        root = tree(ac.registry_text(), blocks=())
        expect("T1 legacy first write", root, False, 0, unchanged=False)
        pre_registry = "\n".join(list(HEADER) + [b + "\n" for b in _BODIES]).rstrip() + "\n"
        if read(root / AGENTS_REL) != legacy_golden or pre_registry.encode("utf-8") != legacy_golden:
            failures.append("T1: the legacy layout is not the pre-registry join: {!r}".format(read(root / AGENTS_REL)))
        expect("T1 legacy --check", root, True, 0)
        expect("T1 legacy rewrite", root, False, 0)

        # T2. Composed layout: golden bytes, and idempotent.
        root = tree(composed_reg)
        expect("T2 composed first write", root, False, 0, unchanged=False)
        if read(root / AGENTS_REL) != golden:
            failures.append("T2: composed bytes differ from the golden layout: {!r}".format(read(root / AGENTS_REL)))
        expect("T2 composed --check", root, True, 0)
        expect("T2 composed rewrite", root, False, 0)
        offsets = ac.compose(AGENTS_REL, HEADER, _BODIES, ac.load_registry(root))
        if offsets.data[offsets.rules_start:offsets.rules_end] != _RULES_INNER.encode("utf-8"):
            failures.append("T2: rules_start/rules_end do not bound the AIQT-RULES inner text")

        # T3-T7. Hand edits and broken marker structure in a composed target.
        bad_a = seg_a.encode("utf-8")
        a_begin = bad_a.split(b"\n", 1)[0]
        z_begin = seg_z.encode("utf-8").split(b"\n", 1)[0]
        rules_begin = seg_rules.encode("utf-8").split(b"\n", 1)[0]
        digest_a = hashlib.sha256(_A_INNER.encode("utf-8")).hexdigest().encode("ascii")
        cases = (
            ("T3 edit inside an OPF block", b"alpha line\n<!-- OPF-A:END", b"alpha lime\n<!-- OPF-A:END"),
            ("T4 edit inside AIQT-RULES", b"Rule text.", b"Rule text!"),
            ("T5 edit in the header", b"# AGENTS.md\n", b"# AGENTZ.md\n"),  # same length: only the header check sees it
            ("T6 text outside any block", rules_begin, b"stray text\n\n" + rules_begin),
            ("T7 duplicated pair", rules_begin, bad_a + b"\n\n" + rules_begin),
            ("T7 missing END", b"\n<!-- OPF-Z:END -->", b""),
            ("T7 orphan END", rules_begin, b"<!-- OPF-A:END -->\n\n" + rules_begin),
            ("T7 nested BEGIN", a_begin + b"\n", a_begin + b"\n" + z_begin + b"\n"),
            ("T7 END naming another block", b"<!-- OPF-A:END -->", b"<!-- OPF-Z:END -->"),
            ("T7 missing AIQT-RULES block", seg_rules.encode("utf-8") + b"\n\n", b""),
            ("T7 lower-case marker", b"<!-- OPF-A:BEGIN", b"<!-- opf-a:BEGIN"),
            ("T7 short digest", digest_a, digest_a[:63]),
            ("T7 marker without (generated", b"OPF-A:BEGIN (generated sha256=", b"OPF-A:BEGIN (sha256="),
            ("T7 marker with a leading space", b"<!-- OPF-A:BEGIN", b" <!-- OPF-A:BEGIN"),
        )
        for label, old, new in cases:
            refused(label, tree(composed_reg, agents=mutated(label, golden, old, new)))

        # T8. An undeclared block is refused; once retired, a regeneration drops it.
        only_a = ac.registry_text([(AGENTS_REL, "composed")], [_BLOCK_A])
        refused("T8 undeclared block id", tree(only_a, blocks=(_BLOCK_A,), agents=golden))
        root = tree(ac.registry_text([(AGENTS_REL, "composed")], [_BLOCK_A], retired=["OPF-Z"]),
                    blocks=(_BLOCK_A,), agents=golden)
        expect("T8 retired block id", root, False, 0, unchanged=False)
        if read(root / AGENTS_REL) != (prefix + seg_a + "\n\n" + seg_rules + "\n").encode("utf-8"):
            failures.append("T8: the retired block was not dropped: {!r}".format(read(root / AGENTS_REL)))

        # T9. Out of registry order with intact digests: drift, and a write restores the order.
        root = tree(composed_reg, agents=(prefix + seg_rules + "\n\n" + seg_a + "\n\n" + seg_z + "\n").encode("utf-8"))
        expect("T9 reordered --check", root, True, 1)
        expect("T9 reordered write", root, False, 0, unchanged=False)
        if read(root / AGENTS_REL) != golden:
            failures.append("T9: a write did not restore the registry order")

        # T10. A legacy target holding a pasted marker line.
        pasted = legacy_golden + b"<!-- NOTE:BEGIN (generated) -->\nhand text\n<!-- NOTE:END -->\n"
        refused("T10 legacy target with a pasted block", tree(ac.registry_text(), blocks=(), agents=pasted))

        # T11. Migration from the legacy layout: only the exact legacy rendering.
        root = tree(composed_reg, agents=legacy_golden)
        expect("T11 migration", root, False, 0, unchanged=False)
        if read(root / AGENTS_REL) != golden:
            failures.append("T11: the migration did not compose the golden layout")
        root = tree(composed_reg, agents=legacy_golden + b"\nA hand-added line.\n")
        expect("T11 migration with extra text", root, False, 2)

        # T12. Registry errors: exit 2 in both modes, nothing written.
        copilot_row = '[[target]]\npath = ".github/copilot-instructions.md"\nlayout = "legacy"\n'
        gemini_row = '[[target]]\npath = "GEMINI.md"\nlayout = "legacy"\n'
        reg_cases = (
            ("format-version 2", "format-version = 1", "format-version = 2"),
            ("format-version bool", "format-version = 1", "format-version = true"),
            ("unknown top-level key", "format-version = 1", "extra = 1\nformat-version = 1"),
            ("no retired-blocks", "retired-blocks = []\n", ""),
            ("unknown target key", 'layout = "composed"', 'layout = "composed"\nnote = "x"'),
            ("a target missing", copilot_row, ""),
            ("a duplicate target", copilot_row, copilot_row + "\n" + gemini_row),
            ("an unknown target", 'path = "GEMINI.md"', 'path = "OTHER.md"'),
            ("a bad layout", 'layout = "composed"', 'layout = "mixed"'),
            ("a bad block id", 'id = "OPF-A"', 'id = "opf-a"'),
            ("a reserved block id", 'id = "OPF-A"', 'id = "AIQT-EXTRA"'),
            ("the rules id as a block", 'id = "OPF-A"', 'id = "AIQT-RULES"'),
            ("a duplicate block id", 'id = "OPF-Z"', 'id = "OPF-A"'),
            ("a duplicate order", 'position = "after-rules"', 'position = "before-rules"'),
            ("a block on a legacy target", 'layout = "composed"', 'layout = "legacy"'),
            ("an adopter owner", 'owner = "opf"', 'owner = "adopter"'),
            ("order zero", "order = 1", "order = 0"),
            ("order bool", "order = 1", "order = true"),
            ("order string", "order = 1", 'order = "1"'),
            ("a bad position", 'position = "after-rules"', 'position = "middle"'),
            ("an unknown block key", 'id = "OPF-A"', 'id = "OPF-A"\nextra = 1'),
            ("a block naming an unknown target", 'target = "AGENTS.md"', 'target = "README.md"'),
            ("a malformed retired id", "retired-blocks = []", 'retired-blocks = ["bad id"]'),
            ("a reserved retired id", "retired-blocks = []", 'retired-blocks = ["AIQT-RULES"]'),
            ("a duplicate retired id", "retired-blocks = []", 'retired-blocks = ["OPF-OLD", "OPF-OLD"]'),
            ("a retired id also declared", "retired-blocks = []", 'retired-blocks = ["OPF-A"]'),
            ("not TOML", "format-version = 1", "format-version = = 1"),
            ("a carriage return", "format-version = 1\n", "format-version = 1\r\n"),
        )
        for label, old, new in reg_cases:
            if old not in composed_reg:
                failures.append("T12 {}: fixture mutation did not apply".format(label))
            root = tree(composed_reg.replace(old, new, 1), agents=golden)
            expect("T12 " + label, root, True, 2)
            expect("T12 " + label, root, False, 2)
        root = tree(None, agents=golden)
        expect("T12 registry absent", root, True, 2)
        expect("T12 registry absent", root, False, 2)
        root = tree(None, agents=golden)
        (root / "reg.toml").write_text(composed_reg, encoding="utf-8")
        os.symlink(root / "reg.toml", root / ".aiqt" / "core" / "adapter-blocks.toml")
        expect("T12 registry is a symlink", root, False, 2)

        # T13. Block source errors: exit 2, nothing written. No AGENTS.md exists, so the write guard has
        #      nothing to refuse and the source rule under test is the only reason for exit 2.
        def with_source(source, text):
            return ("OPF-A", "AGENTS.md", "before-rules", 1, source, text)

        src_cases = (
            ("an absolute source", "/etc/hostname", None),
            ("a .. source", "opf/../opf/blocks/a.md", None),
            ("a source equal to a target", "GEMINI.md", "alpha line\n"),
            ("a source equal to the registry", ".aiqt/core/adapter-blocks.toml", None),
            ("an absent source", "opf/blocks/a.md", None),
            ("an invalid UTF-8 source", "opf/blocks/a.md", b"alpha \xff line\n"),
            ("a source with a CR", "opf/blocks/a.md", "alpha line\r\n"),
            ("a source with a BOM", "opf/blocks/a.md", chr(0xFEFF) + "alpha line\n"),
            ("a source with a NUL", "opf/blocks/a.md", "alpha" + chr(0) + " line\n"),
            ("an empty source", "opf/blocks/a.md", ""),
            ("a source with no final LF", "opf/blocks/a.md", "alpha line"),
            ("a source ending in two LFs", "opf/blocks/a.md", "alpha line\n\n"),
            ("a source with a leading blank line", "opf/blocks/a.md", "\nalpha line\n"),
            ("a source with a trailing blank line", "opf/blocks/a.md", "alpha line\n \n"),
            ("a source with a marker-like line", "opf/blocks/a.md", "alpha line\n<!-- X:END -->\n"),
        )
        for label, source, text in src_cases:
            block = with_source(source, text)
            root = tree(ac.registry_text([(AGENTS_REL, "composed")], [block]),
                        blocks=(block,) if text is not None else ())
            expect("T13 " + label, root, True, 2)
            expect("T13 " + label, root, False, 2)
        for label, link_dir in (("a symlinked source file", False), ("a symlinked source directory", True)):
            root = tree(ac.registry_text([(AGENTS_REL, "composed")], [_BLOCK_A]), blocks=())
            real = root / "real"
            real.mkdir()
            (real / "a.md").write_text(_A_INNER + "\n", encoding="utf-8")
            (root / "opf").mkdir()
            if link_dir:
                os.symlink(real, root / "opf" / "blocks")
            else:
                (root / "opf" / "blocks").mkdir()
                os.symlink(real / "a.md", root / "opf" / "blocks" / "a.md")
            expect("T13 " + label, root, True, 2)
            expect("T13 " + label, root, False, 2)
        root = tree(ac.registry_text([(AGENTS_REL, "composed")], [_BLOCK_A]), blocks=())
        (root / "opf" / "blocks" / "a.md").mkdir(parents=True)
        expect("T13 a directory as source", root, True, 2)
        expect("T13 a directory as source", root, False, 2)

        # T14. A CRLF-converted legacy AGENTS.md is drift (raw bytes, not decoded text).
        root = tree(ac.registry_text(), blocks=(), agents=legacy_golden.replace(b"\n", b"\r\n"))
        expect("T14 CRLF legacy --check", root, True, 1)

        # T15. The gen_adapters pair in one run: a refused GEMINI.md means copilot is not written.
        block_g = ("OPF-G", "GEMINI.md", "before-rules", 1, "opf/blocks/g.md", "gemini line\n")
        gem_reg = ac.registry_text([("GEMINI.md", "composed")], [block_g])
        root = tree(gem_reg, blocks=(block_g,))
        expect("T15 adapters first write", root, False, 0, targets=adapter_pair, unchanged=False)
        gem = root / "GEMINI.md"
        copilot = root / ".github" / "copilot-instructions.md"
        gem.write_bytes(mutated("T15", gem.read_bytes(), b"gemini line", b"gemini lime"))
        copilot.unlink()
        expect("T15 adapters with GEMINI.md refused", root, False, 2, targets=adapter_pair)
        if copilot.exists():
            failures.append("T15: copilot-instructions.md was written although GEMINI.md was refused")

        # T16. No rule corpus: a composed target is kept (exit 2); a legacy target is an orphan.
        root = tree(composed_reg, agents=golden)
        shutil.rmtree(root / ".aiqt" / "core" / "rules")
        expect("T16 composed, no corpus", root, True, 2)
        expect("T16 composed, no corpus", root, False, 2)
        root = tree(ac.registry_text(), blocks=(), agents=legacy_golden)
        shutil.rmtree(root / ".aiqt" / "core" / "rules")
        expect("T16 legacy, no corpus --check", root, True, 1)
        expect("T16 legacy, no corpus write", root, False, 0, unchanged=False)
        if (root / AGENTS_REL).exists():
            failures.append("T16: a legacy AGENTS.md with no corpus was not removed")

        # T17. A corpus path that exists but cannot be stat'ed (a symlink loop) is exit 2, never an absent
        #      corpus that deletes the adapters (Path.is_dir() reads it as absent).
        root = tree(ac.registry_text(), blocks=())
        expect("T17 adapters first write", root, False, 0, targets=adapter_pair, unchanged=False)
        shutil.rmtree(root / ".aiqt" / "core" / "rules")
        os.symlink("rules", root / ".aiqt" / "core" / "rules")
        expect("T17 unreachable corpus", root, False, 2, targets=adapter_pair)
        if not ((root / "GEMINI.md").exists() and (root / ".github" / "copilot-instructions.md").exists()):
            failures.append("T17: an adapter was deleted on an unreachable corpus")

        # T18. A rule body holding a marker-like line cannot be composed.
        marked = _RULE.replace("Rule text.", "Rule text.\n\n<!-- RULE-X:BEGIN (generated) -->")
        root = tree(composed_reg, rules=(_APEX, marked), agents=golden)
        expect("T18 marker-like rule body", root, True, 2)
        expect("T18 marker-like rule body", root, False, 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: the legacy layout is the pre-registry join and the composed layout matches its "
          "golden bytes, both idempotent; a hand edit in a block, in AIQT-RULES or in the header, text "
          "outside a block, and every broken or malformed marker are drift (exit 1) and refused (exit 2) "
          "with nothing written; an undeclared block is refused and a retired one dropped; registry order "
          "is restored; a marker in a legacy target is refused; only the exact legacy rendering migrates; "
          "every registry and block-source error is exit 2; CRLF is drift; one refused adapter blocks the "
          "other's write; a composed target with no corpus is kept and a legacy one removed; an "
          "unreachable corpus deletes nothing; a marker-like rule body is exit 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
