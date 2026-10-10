#!/usr/bin/env python3
"""Generate AGENTS.md (the Codex adapter) from the .aiqt/core/rules/ sources.

Same core that feeds Claude's .claude/rules/ tree, so a Codex session gets identical AIQT governance. The
rules are concatenated in AIQT priority order (apex; then Accuracy, Integrity, Quality, Trust at tier 10;
then Progress, Speed, Cost; then the security family in CIA-plus-privacy order), each rule's title demoted
to a section heading. The layout (legacy, or composed with reviewed blocks around the rules) comes from the
block registry .aiqt/core/adapter-blocks.toml through tools/_adapter_compose.py, the engine this generator
shares with gen_adapters.py. --check drift-gates AGENTS.md byte for byte; exit 2 on a malformed source or
registry, a symlinked target, an absent rule corpus (nothing is deleted), or a write that would erase a
hand edit in a composed-layout target. A legacy-layout target is refused only when it holds a pasted
marker-like line; any other hand edit in it is lost on regeneration, though --check still reports it as
drift.

  gen_agents.py              regenerate AGENTS.md
  gen_agents.py --check      exit 1 if AGENTS.md differs from a fresh composition; write nothing
  gen_agents.py --self-test  synthetic trees for the composition engine's fail-closed matrix
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_agents.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

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
#   T12 each registry error: exit 2 in both modes, nothing written; every field of the wrong TOML type
#       (an array, a table, a number or a boolean) is a clean refusal naming the field and stating the
#       type rule it broke, never an exception.
#   T13 each block-source error: exit 2, nothing written.
#   T14 a CRLF-converted legacy AGENTS.md: --check 1.
#   T15 two targets in one run (the gen_adapters pair) with either one refused: the other is not written.
#   T16 no rule corpus: exit 2 in both modes and nothing deleted, whether the target is composed, legacy,
#       legacy holding a pasted block, the gen_adapters pair, or absent (nothing is created either).
#   T17 a rule corpus path that exists but cannot be stat'ed (a symlink loop): exit 2, nothing deleted.
#   T18 a rule body holding a marker-like line, composed target: exit 2.
#   T19 a target that is a symlink (to a file outside the tree, or dangling), a target under a symlinked
#       directory, and a target that is a directory: exit 2 in both modes; the outside file and directory
#       and the sibling target are unchanged, and nothing is created through the link.
#   T20 a legacy target already in sync is not judged: a rule body holding a marker-like line writes once
#       and then rewrites as exit 0 (the guard runs only on a target whose bytes would change).
#   T21 a write keeps the target's ordinary permission bits and drops its set-user-ID and sticky bits
#       (umask pinned; the kept bits hold execute bits no creation mode supplies, so a dropped fchmod
#       cannot pass, and the whole S_IMODE is asserted, so a kept sticky bit, which no write clears, fails)
#       and leaves no temporary file; a failed rename is exit 2 with the target unchanged and the
#       temporary file left in place and named in the refusal.
#   T22 a parent directory swapped for a symlink after the write-side walk: the write lands through the
#       held descriptor in the original directory and nothing outside the tree changes.
#   T23 the same swap after the read-side walk: the bytes read are the real target's, never the planted
#       outside file's.
#   T24 the target swapped for a symlink after the walk's stat: the no-follow open refuses (exit 2) and
#       the outside file is never read.
#   T25 the target swapped for a FIFO after the walk's stat: the fstat re-check refuses (exit 2).
#   T26 a symlink planted where mkdir was about to make a missing parent: the no-follow re-open refuses
#       (exit 2) and nothing lands outside the tree.
#   T27 a pre-existing file at a colliding temporary name survives: every name colliding is exit 2 with
#       the file kept; one collision is retried and the write succeeds, the file still kept.
#   T28 an existing target without write permission is refused (exit 2) with its bytes and mode kept and
#       no temporary file left (skipped where the runner can write it anyway, e.g. root).
#   T29 a platform without dir_fd support is exit 2 in both modes: no path-based fallback.
#   T30 the target swapped for a symlink after the write-side walk: the write probe's no-follow open
#       refuses (exit 2), the link and the outside file are unchanged and no temporary file is made.
#   T31 the target swapped for a FIFO with a reader held open after the write-side walk: the fstat check
#       on the probe descriptor refuses (exit 2) and the FIFO is not replaced.
#   T32 a nested target swapped for a FIFO with no reader after the write-side walk: the probe's open
#       fails at once with ENXIO, refused (exit 2) in the engine's wording naming the path from the root;
#       a probe descriptor opened in blocking mode fails the vector, and a watchdog whose clock starts
#       only once the probe's open is entered supplies a reader, so a blocking open cannot hang the run.
#   T33 a foreign file put at the temporary name after its creation, then a failed rename: exit 2, the
#       target unchanged, the foreign file kept and its name given in the refusal (nothing is unlinked).
#   T34 descriptor accounting: the descriptors open after every other vector (T1-T33, then T35 to T38,
#       which run before it) are the ones open before them, so a close dropped on any ordinary success or
#       error path fails (skipped where neither /proc/self/fd nor /dev/fd can be listed).
#   T35 on a nested target, an exception other than OSError raised at the rename (a stand-in for an
#       interrupt) propagates with a note naming the temporary file it left by its path from the root;
#       the target unchanged and the file left there.
#   T36 a nested target swapped for a directory after the write-side walk: the probe's open fails with
#       EISDIR, refused (exit 2) in the engine's wording naming the path from the root; nothing replaced.
#   T37 a nested target swapped for a directory after the read-side walk: the read leg's fstat, made
#       before any file object, refuses it (exit 2 in both modes) in the engine's wording naming the path
#       from the root; nothing replaced.
#   T38 a nested target swapped for a UNIX socket after the read-side walk: the read leg's open fails
#       with ENXIO, refused (exit 2 in both modes) in the engine's wording naming the path from the root
#       (ENXIO is injected where this environment cannot bind the socket or refuses its open otherwise).

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
    import errno
    import hashlib
    import io
    import os
    import shutil
    import socket
    import stat
    import tempfile
    import threading
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
        before = [read(root / rel) for rel in rels] if unchanged else None
        try:
            code, out = drive(root, check, targets)
        except Exception as exc:  # the engine must map every input error to an exit code, never raise
            failures.append("{}: {} raised {}: {}".format(
                label, "--check" if check else "write", type(exc).__name__, exc))
            return ""
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

    def open_fds():
        for fd_dir in ("/proc/self/fd", "/dev/fd"):
            try:
                return sorted(os.listdir(fd_dir))
            except OSError:
                continue
        return None

    fds_before = open_fds()
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
        # Wrong TOML types: each of these 26 values is exit 2 in both modes, and the refusal names the
        # field AND states the type rule it broke. The stated rule is what discriminates each _string,
        # _plain_int, _valid_id or _check_rel check from the membership refusal behind it, which would
        # also refuse cleanly and also name the field (retired-blocks takes two message forms, so only
        # its field name is pinned).
        type_cases = (
            ("block target", 'target = "AGENTS.md"', ('["AGENTS.md"]', "{ p = 1 }", "1", "true"),
             "must be a string"),
            ("block owner", 'owner = "opf"', ('["opf"]', '{ o = "opf" }', "1"), "must be a string"),
            ("block position", 'position = "after-rules"', ('["after-rules"]', "{ p = 1 }", "2"),
             "must be a string"),
            ("block order", "order = 1", ("1.0", "[1]", "{ o = 1 }"), "must be a positive integer"),
            ("block source", 'source = "opf/blocks/a.md"', ('["opf/blocks/a.md"]', "{ s = 1 }", "1"),
             "must be a non-empty string"),
            ("block id", 'id = "OPF-A"', ('["OPF-A"]', "{ i = 1 }", "1"), "is not of the form"),
            ("target-row path", 'path = "GEMINI.md"', ('["GEMINI.md"]', "{ p = 1 }"), "must be a string"),
            ("target-row layout", 'layout = "composed"', ('["composed"]', "{ l = 1 }"),
             "must be a string"),
            ("retired-blocks", "retired-blocks = []", ("{}", "[{}]", "[1]"), None),
        )
        for field, old, values, rule in type_cases:
            if old not in composed_reg:
                failures.append("T12 {}: fixture mutation did not apply".format(field))
            key = old.split(" = ", 1)[0]
            for value in values:
                label = "T12 {} = {}".format(field, value)
                root = tree(composed_reg.replace(old, key + " = " + value, 1), agents=golden)
                for check in (True, False):
                    out = expect(label, root, check, 2)
                    if key not in out:
                        failures.append("{}: the refusal does not name the field {}: {}".format(
                            label, key, out.strip()))
                    if rule is not None and rule not in out:
                        failures.append("{}: the refusal does not state the type rule {!r}: {}".format(
                            label, rule, out.strip()))
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
        # The mirror: the SECOND target (copilot) refused, so the first (GEMINI.md, absent) is not written.
        pasted_block = b"<!-- NOTE:BEGIN (generated) -->\nhand text\n<!-- NOTE:END -->\n"
        root = tree(ac.registry_text(), blocks=())
        expect("T15 mirror first write", root, False, 0, targets=adapter_pair, unchanged=False)
        gem, copilot = root / "GEMINI.md", root / ".github" / "copilot-instructions.md"
        copilot.write_bytes(copilot.read_bytes() + pasted_block)
        gem.unlink()
        expect("T15 adapters with copilot refused", root, False, 2, targets=adapter_pair)
        if gem.exists():
            failures.append("T15: GEMINI.md was written although copilot-instructions.md was refused")

        # T16. No rule corpus: exit 2 in both modes, nothing deleted and nothing created.
        no_corpus = (
            ("composed", composed_reg, (_BLOCK_A, _BLOCK_Z), golden, agents_only),
            ("legacy", ac.registry_text(), (), legacy_golden, agents_only),
            ("legacy holding a pasted block", ac.registry_text(), (), pasted_block, agents_only),
            ("no target", ac.registry_text(), (), None, agents_only),
            ("the gen_adapters pair", ac.registry_text(), (), None, adapter_pair),
        )
        for label, registry, blocks, agents, targets in no_corpus:
            root = tree(registry, blocks=blocks, agents=agents)
            if targets is adapter_pair:
                for rel, _h in adapter_pair:
                    (root / rel).parent.mkdir(parents=True, exist_ok=True)
                    (root / rel).write_bytes(legacy_golden)
            shutil.rmtree(root / ".aiqt" / "core" / "rules")
            for check in (True, False):
                out = expect("T16 no corpus, " + label, root, check, 2, targets=targets)
                if "nothing was written or deleted" not in out:
                    failures.append("T16 no corpus, {}: no refusal message: {}".format(label, out.strip()))
            if read(root / AGENTS_REL) != agents:
                failures.append("T16 no corpus, {}: AGENTS.md was not kept as it was".format(label))

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

        # T19. Symlinked and non-regular targets: exit 2 in both modes, nothing outside the tree touched.
        outside = tmp / "outside"
        outside.mkdir()
        victim = outside / "victim.md"
        new_rule = _RULE.replace("Rule text.", "New text.")
        for label, link_dir in (("a symlinked target", False), ("a symlinked parent directory", True)):
            victim.write_bytes(legacy_golden)
            root = tree(ac.registry_text(), blocks=())
            expect("T19 first write", root, False, 0, targets=adapter_pair, unchanged=False)
            gem, copilot = root / "GEMINI.md", root / ".github" / "copilot-instructions.md"
            # A rule change makes every target drift, so a write would rewrite each one.
            (root / ".aiqt" / "core" / "rules" / "r1.md").write_text(new_rule, encoding="utf-8")
            if link_dir:
                (outside / "gh").mkdir(exist_ok=True)
                (outside / "gh" / "copilot-instructions.md").write_bytes(copilot.read_bytes())
                shutil.rmtree(root / ".github")
                os.symlink(outside / "gh", root / ".github")
                watched = outside / "gh" / "copilot-instructions.md"
            else:
                copilot.unlink()
                os.symlink(victim, copilot)
                watched = victim
            outside_before = sorted(str(p) for p in outside.rglob("*")), read(watched)
            gem_before = read(gem)
            for check in (True, False):
                out = expect("T19 " + label, root, check, 2, targets=adapter_pair)
                if "symlink" not in out:
                    failures.append("T19 {}: the refusal does not name the symlink: {}".format(
                        label, out.strip()))
            if (sorted(str(p) for p in outside.rglob("*")), read(watched)) != outside_before:
                failures.append("T19 {}: a file outside the tree was written or created".format(label))
            if read(gem) != gem_before:
                failures.append("T19 {}: the sibling target GEMINI.md was written".format(label))
        root = tree(ac.registry_text(), blocks=())
        os.symlink(outside / "absent.md", root / AGENTS_REL)
        expect("T19 a dangling symlinked target", root, True, 2)
        expect("T19 a dangling symlinked target", root, False, 2)
        if (outside / "absent.md").exists():
            failures.append("T19: a dangling symlinked target created a file outside the tree")
        root = tree(ac.registry_text(), blocks=())
        (root / AGENTS_REL).mkdir()
        expect("T19 a directory as target", root, True, 2, unchanged=False)  # read() cannot read a directory
        expect("T19 a directory as target", root, False, 2, unchanged=False)
        if not (root / AGENTS_REL).is_dir() or any((root / AGENTS_REL).iterdir()):
            failures.append("T19: a directory target was replaced or written into")

        # T20. A legacy target in sync is not judged, so a rule body holding a marker-like line rewrites.
        marked = _RULE.replace("Rule text.", "Rule text.\n\n<!-- EXAMPLE:BEGIN -->")
        root = tree(ac.registry_text(), blocks=(), rules=(_APEX, marked))
        expect("T20 legacy marker-like body, first write", root, False, 0, unchanged=False)
        expect("T20 legacy marker-like body, --check", root, True, 0)
        expect("T20 legacy marker-like body, rewrite", root, False, 0)

        # T21. A write keeps the mode and leaves no temporary file; a failed rename leaves the target as it was.
        #      The umask is pinned (and restored) and the asserted mode holds execute bits, which the
        #      temporary file's creation mode (0o666 before the umask) can never supply, so a dropped
        #      fchmod cannot pass under ANY ambient umask.
        saved_umask = os.umask(0o022)
        try:
            root = tree(ac.registry_text(), blocks=(), agents=legacy_golden)
            # Set-user-ID and sticky bits beyond the ordinary 0o751: the write drops both, and the sticky
            # bit (which no write clears) makes a kept one visible even to an unprivileged runner.
            try:
                os.chmod(root / AGENTS_REL, 0o5751)
            except OSError:  # a platform refusing the sticky bit on a regular file (EFTYPE)
                os.chmod(root / AGENTS_REL, 0o4751)
            if not stat.S_IMODE(os.stat(root / AGENTS_REL).st_mode) & 0o7000:
                failures.append("T21: the fixture could not set a mode bit beyond 0o777")
            (root / ".aiqt" / "core" / "rules" / "r1.md").write_text(new_rule, encoding="utf-8")
            expect("T21 rewrite", root, False, 0, unchanged=False)
            if read(root / AGENTS_REL) == legacy_golden:
                failures.append("T21: the rewrite did not change AGENTS.md")
            kept = stat.S_IMODE(os.stat(root / AGENTS_REL).st_mode)
            if kept != 0o751:
                failures.append("T21: the rewrite left mode {:o}, not the target's ordinary permission bits "
                                "751".format(kept))
            if sorted(p.name for p in root.iterdir()) != [".aiqt", AGENTS_REL]:
                failures.append("T21: the write left a stray file: {}".format(sorted(p.name for p in root.iterdir())))
            (root / ".aiqt" / "core" / "rules" / "r1.md").write_text(_RULE, encoding="utf-8")
            real_replace = ac.os.replace

            def failing_replace(*_a, **_k):
                raise OSError("injected rename failure")

            ac.os.replace = failing_replace
            try:
                out = expect("T21 failed rename", root, False, 2)
            finally:
                ac.os.replace = real_replace
            left = [p.name for p in root.iterdir() if p.name not in (".aiqt", AGENTS_REL)]
            if len(left) != 1 or not left[0].startswith("." + AGENTS_REL + ".") or not left[0].endswith(".tmp"):
                failures.append("T21: a failed rename did not leave exactly its temporary file: {}".format(left))
            elif left[0] not in out:
                failures.append("T21: the refusal does not name the temporary file left: {}".format(out.strip()))
        finally:
            os.umask(saved_umask)

        # T22. A parent directory swapped for a symlink AFTER the write-side walk cannot redirect the
        #      write: the temporary create and the rename go through the directory descriptor the walk
        #      opened, so the bytes land in the original directory and nothing outside the tree changes
        #      (a directory renamed out of the tree, rather than within it as here, still receives the
        #      write at its new place: a disclosed residual of the engine).
        #      The swap runs inside the first token_hex call, which _write_target makes only after
        #      _target_dir_fd has judged the whole path; reverting to path-based writes makes this
        #      vector fail (the swapped parent would redirect the temporary file outside the tree).
        copilot_rel = ".github/copilot-instructions.md"
        copilot_only = [(copilot_rel, ("# Copilot", "", "Stand-in header.", ""))]
        root = tree(ac.registry_text(), blocks=())
        expect("T22 first write", root, False, 0, targets=copilot_only, unchanged=False)
        (root / ".aiqt" / "core" / "rules" / "r1.md").write_text(new_rule, encoding="utf-8")
        lair = tmp / "t22-outside"
        (lair / "gh").mkdir(parents=True)
        (lair / "gh" / "copilot-instructions.md").write_bytes(b"OUTSIDE\n")
        real_token_hex = ac.secrets.token_hex
        swapped = []

        def swapping_token_hex(n):
            if not swapped:
                swapped.append(True)
                (root / ".github").rename(root / "gh-held")
                os.symlink(lair / "gh", root / ".github")
            return real_token_hex(n)

        ac.secrets.token_hex = swapping_token_hex
        try:
            expect("T22 write with the parent swapped mid-run", root, False, 0, targets=copilot_only,
                   unchanged=False)
        finally:
            ac.secrets.token_hex = real_token_hex
        if not swapped:
            failures.append("T22: the swap hook did not run")
        if ((lair / "gh" / "copilot-instructions.md").read_bytes() != b"OUTSIDE\n"
                or sorted(p.name for p in (lair / "gh").iterdir()) != ["copilot-instructions.md"]):
            failures.append("T22: the swapped parent redirected the write outside the tree")
        held = read(root / "gh-held" / "copilot-instructions.md")
        if held is None or b"New text." not in held:
            failures.append("T22: the write did not land in the directory the descriptor held")

        # T23. The same swap after the read-side walk: the open goes through the held descriptor, so the
        #      bytes read are the real target's and --check stays clean; a path-based re-open would read
        #      the planted outside file and report drift.
        root = tree(ac.registry_text(), blocks=())
        expect("T23 first write", root, False, 0, targets=copilot_only, unchanged=False)
        lair = tmp / "t23-outside"
        (lair / "gh").mkdir(parents=True)
        (lair / "gh" / "copilot-instructions.md").write_bytes(b"OUTSIDE\n")
        real_walk = ac._target_dir_fd
        swapped = []

        def read_swap_walk(*a, **k):
            result = real_walk(*a, **k)
            if not swapped:
                swapped.append(True)
                (root / ".github").rename(root / "gh-held")
                os.symlink(lair / "gh", root / ".github")
            return result

        ac._target_dir_fd = read_swap_walk
        try:
            expect("T23 --check with the parent swapped mid-run", root, True, 0, targets=copilot_only,
                   unchanged=False)
        finally:
            ac._target_dir_fd = real_walk
        if not swapped:
            failures.append("T23: the swap hook did not run")
        if (lair / "gh" / "copilot-instructions.md").read_bytes() != b"OUTSIDE\n":
            failures.append("T23: the outside file was touched by a read")

        # T24. The target itself swapped for a symlink after the walk's stat: the no-follow open refuses
        #      (exit 2) in both modes; without O_NOFOLLOW the planted bytes would read back as mere
        #      drift (exit 1), never 2.
        planted = tmp / "t24-outside.md"
        planted.write_bytes(b"outside line\n")
        root = tree(ac.registry_text(), blocks=(), agents=legacy_golden)
        for check in (True, False):
            (root / AGENTS_REL).unlink(missing_ok=True)
            (root / AGENTS_REL).write_bytes(legacy_golden)
            swapped = []

            def late_symlink(*a, **k):
                result = real_walk(*a, **k)
                if not swapped:
                    swapped.append(True)
                    (root / AGENTS_REL).unlink()
                    os.symlink(planted, root / AGENTS_REL)
                return result

            ac._target_dir_fd = late_symlink
            try:
                out = expect("T24 target swapped for a symlink after the walk", root, check, 2,
                             unchanged=False)
            finally:
                ac._target_dir_fd = real_walk
            if "symlink" not in out:
                failures.append("T24: the refusal does not name the symlink: {}".format(out.strip()))
            if planted.read_bytes() != b"outside line\n":
                failures.append("T24: the outside file was changed")

        # T25. The target swapped for a FIFO after the walk's stat: the fstat re-check on the opened
        #      descriptor refuses (exit 2); without it the non-blocking read would return empty bytes
        #      and report mere drift.
        root = tree(ac.registry_text(), blocks=(), agents=legacy_golden)
        swapped = []

        def late_fifo(*a, **k):
            result = real_walk(*a, **k)
            if not swapped:
                swapped.append(True)
                (root / AGENTS_REL).unlink()
                os.mkfifo(root / AGENTS_REL)
            return result

        ac._target_dir_fd = late_fifo
        try:
            out = expect("T25 target swapped for a FIFO after the walk", root, True, 2, unchanged=False)
        finally:
            ac._target_dir_fd = real_walk
        if "not a regular file" not in out:
            failures.append("T25: the refusal does not say the target is not a regular file: {}".format(
                out.strip()))

        # T26. A symlink planted where mkdir was about to make a missing parent: mkdir loses the race
        #      (FileExistsError) and the no-follow re-open judges the winner, refusing the symlink, so
        #      nothing is created outside the tree.
        root = tree(ac.registry_text(), blocks=())
        lair = tmp / "t26-outside"
        lair.mkdir()
        real_mkdir = ac.os.mkdir
        planted_racing = []

        def racing_mkdir(path, *a, **k):
            if k.get("dir_fd") is not None:
                planted_racing.append(True)
                os.symlink(lair, path, dir_fd=k["dir_fd"])
                raise FileExistsError(17, "File exists", path)
            return real_mkdir(path, *a, **k)

        ac.os.mkdir = racing_mkdir
        try:
            out = expect("T26 a symlink planted against mkdir", root, False, 2, targets=copilot_only)
        finally:
            ac.os.mkdir = real_mkdir
        if not planted_racing:
            failures.append("T26: the planted-symlink hook did not run")
        if "symlink" not in out:
            failures.append("T26: the refusal does not name the symlink: {}".format(out.strip()))
        if sorted(lair.iterdir()):
            failures.append("T26: the planted symlink redirected a write outside the tree")

        # T27. A pre-existing file at a colliding temporary name is never deleted: with every candidate
        #      name colliding the write refuses (exit 2) and the file survives; with one collision the
        #      write retries a fresh name, succeeds, and the file still survives.
        root = tree(ac.registry_text(), blocks=(), agents=legacy_golden)
        (root / ".aiqt" / "core" / "rules" / "r1.md").write_text(new_rule, encoding="utf-8")
        fixed = "0" * 16
        squat = root / ".{}.{}.tmp".format(AGENTS_REL, fixed)
        squat.write_bytes(b"not ours\n")
        ac.secrets.token_hex = lambda n: fixed
        try:
            expect("T27 every temporary name colliding", root, False, 2)
        finally:
            ac.secrets.token_hex = real_token_hex
        if read(squat) != b"not ours\n":
            failures.append("T27: a pre-existing colliding file was deleted or rewritten on refusal")
        collide_calls = [0]

        def collide_once(n):
            collide_calls[0] += 1
            return fixed if collide_calls[0] == 1 else real_token_hex(n)

        ac.secrets.token_hex = collide_once
        try:
            expect("T27 one collision then a fresh name", root, False, 0, unchanged=False)
        finally:
            ac.secrets.token_hex = real_token_hex
        if read(squat) != b"not ours\n":
            failures.append("T27: the retry deleted the pre-existing colliding file")
        if read(root / AGENTS_REL) == legacy_golden:
            failures.append("T27: the retried write did not update AGENTS.md")
        squat.unlink(missing_ok=True)  # missing_ok: a faulty engine may have deleted it; T27 already failed then

        # T28. An existing target without write permission is refused (exit 2) with its bytes and mode
        #      kept and no temporary file made, as the plain overwrite before descriptors was. Skipped
        #      where the runner can write a mode-0444 file anyway (root/DAC bypass), observed via
        #      os.access, as conformance.py's unreadable-dir cases do.
        root = tree(ac.registry_text(), blocks=(), agents=legacy_golden)
        (root / ".aiqt" / "core" / "rules" / "r1.md").write_text(new_rule, encoding="utf-8")
        os.chmod(root / AGENTS_REL, 0o444)
        if not os.access(root / AGENTS_REL, os.W_OK):
            expect("T28 read-only target", root, False, 2)
            if os.stat(root / AGENTS_REL).st_mode & 0o777 != 0o444:
                failures.append("T28: the refusal changed the target's mode")
            if sorted(p.name for p in root.iterdir()) != [".aiqt", AGENTS_REL]:
                failures.append("T28: the refusal left a temporary file: {}".format(
                    sorted(p.name for p in root.iterdir())))
        os.chmod(root / AGENTS_REL, 0o644)

        # T29. A platform without dir_fd support refuses (exit 2) in both modes rather than fall back to
        #      a path-based read or write.
        root = tree(ac.registry_text(), blocks=(), agents=legacy_golden)
        (root / ".aiqt" / "core" / "rules" / "r1.md").write_text(new_rule, encoding="utf-8")
        real_supports = ac.os.supports_dir_fd
        ac.os.supports_dir_fd = frozenset()
        try:
            out = expect("T29 no dir_fd support", root, True, 2)
            expect("T29 no dir_fd support", root, False, 2)
        finally:
            ac.os.supports_dir_fd = real_supports
        if "directory descriptor" not in out:
            failures.append("T29: the refusal does not explain the missing capability: {}".format(
                out.strip()))

        # T30-T32 swap the target on the WRITE leg: the hook runs after the _target_dir_fd call that
        # _write_target makes (make_dirs=True), so the read leg has already read the regular file and only
        # the write probe stands between the swap and the rename.
        def write_leg_swap(swap):
            fired = []

            def walk(*a, **k):
                result = real_walk(*a, **k)
                if k.get("make_dirs") and not fired:
                    fired.append(True)
                    swap()
                return result
            return walk, fired

        def strays(root):
            return sorted(p.name for p in root.iterdir() if p.name not in (".aiqt", AGENTS_REL))

        # T30. A symlink swapped in at the target after the write-side walk: the probe's O_NOFOLLOW open
        #      refuses (exit 2); without O_NOFOLLOW the probe opens the outside file through the link, it
        #      passes the regular-file check, and the rename replaces the link (exit 0).
        planted = tmp / "t30-outside.md"
        planted.write_bytes(b"outside line\n")
        root = tree(ac.registry_text(), blocks=(), agents=legacy_golden)
        (root / ".aiqt" / "core" / "rules" / "r1.md").write_text(new_rule, encoding="utf-8")

        def to_symlink():
            (root / AGENTS_REL).unlink()
            os.symlink(planted, root / AGENTS_REL)

        ac._target_dir_fd, fired = write_leg_swap(to_symlink)
        try:
            out = expect("T30 target swapped for a symlink after the write-side walk", root, False, 2,
                         unchanged=False)
        finally:
            ac._target_dir_fd = real_walk
        if not fired:
            failures.append("T30: the write-leg swap hook did not run")
        if "symlink" not in out:
            failures.append("T30: the refusal does not name the symlink: {}".format(out.strip()))
        if planted.read_bytes() != b"outside line\n" or not (root / AGENTS_REL).is_symlink():
            failures.append("T30: the outside file or the planted link was changed")
        if strays(root):
            failures.append("T30: the refusal left a temporary file: {}".format(strays(root)))

        # T31. A FIFO swapped in after the write-side walk, a reader held open so the non-blocking probe
        #      open succeeds: the fstat check on the probe descriptor refuses (exit 2); without it the
        #      rename replaces the FIFO with the generated bytes (exit 0).
        root = tree(ac.registry_text(), blocks=(), agents=legacy_golden)
        (root / ".aiqt" / "core" / "rules" / "r1.md").write_text(new_rule, encoding="utf-8")
        readers = []

        def to_held_fifo():
            (root / AGENTS_REL).unlink()
            os.mkfifo(root / AGENTS_REL)
            readers.append(os.open(root / AGENTS_REL, os.O_RDONLY | os.O_NONBLOCK))

        ac._target_dir_fd, fired = write_leg_swap(to_held_fifo)
        try:
            out = expect("T31 target swapped for a FIFO with a reader after the write-side walk", root, False,
                         2, unchanged=False)
        finally:
            ac._target_dir_fd = real_walk
            for rfd in readers:
                os.close(rfd)
        if not fired:
            failures.append("T31: the write-leg swap hook did not run")
        if "not a regular file" not in out:
            failures.append("T31: the refusal does not say the target is not a regular file: {}".format(
                out.strip()))
        if not stat.S_ISFIFO(os.lstat(root / AGENTS_REL).st_mode) or strays(root):
            failures.append("T31: the FIFO was replaced or a temporary file was left: {}".format(strays(root)))

        # T32. A nested target swapped for a FIFO with no reader after the write-side walk: the probe's
        #      O_NONBLOCK open fails at once (ENXIO), refused (exit 2) in the engine's wording naming the
        #      path from the root. The judgement is not a timer: the engine's probe open on the FIFO is
        #      observed through a wrapper, and a probe descriptor in blocking mode fails the vector, since a
        #      blocking open of a FIFO with no reader waits for one. A watchdog keeps the run bounded: its
        #      clock starts only when the wrapper is entered for the probe's open (or the run has ended),
        #      and on expiry it opens a reader on the FIFO, which releases a blocking open. A stall anywhere
        #      before the probe therefore starts no clock: the healthy probe meets a FIFO with no reader and
        #      fails with ENXIO, so the ENXIO wording stays judged. Only a stall of the watchdog's five
        #      seconds between the wrapper's signal and its own open, two adjacent statements, lets the
        #      reader arrive first; the probe then still opens the FIFO in non-blocking mode and the fstat
        #      check refuses it (exit 2, the same wording), so even that cannot fail a healthy engine.
        nested = ".github/copilot-instructions.md"
        nested_target = [(nested, ("# Copilot", "", "Stand-in header.", ""))]
        nested_name = nested.rsplit("/", 1)[1]
        nested_refusal = "target {} is not a regular file".format(nested)

        def nested_strays(root):
            return sorted(p.name for p in (root / ".github").iterdir() if p.name != nested_name)

        root = tree(ac.registry_text(), blocks=())
        (root / ".github").mkdir()
        (root / nested).write_bytes(b"old line\n")
        ready, finished = threading.Event(), threading.Event()
        rescuers, probes = [], []
        real_open = ac.os.open

        def to_fifo():
            (root / nested).unlink()
            os.mkfifo(root / nested)

        def watchdog():
            ready.wait()  # set as the probe's open is entered, or by the finally below if the run ends first
            if not finished.wait(5):
                rescuers.append(real_open(root / nested, os.O_RDONLY | os.O_NONBLOCK))

        def probe_open(path, flags, *a, **k):
            # Only the write probe opens the final name write-only without O_CREAT (the temporary file is
            # created, the read leg opens read-only); every other open passes straight through.
            if (path != nested_name or flags & os.O_CREAT
                    or flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR) != os.O_WRONLY):
                return real_open(path, flags, *a, **k)
            ready.set()  # the watchdog's clock starts here, at probe entry, never before
            try:
                fd = real_open(path, flags, *a, **k)
            except OSError as exc:
                probes.append(("raised", exc.errno))
                raise
            probes.append(("opened", os.get_blocking(fd)))
            return fd

        ac._target_dir_fd, fired = write_leg_swap(to_fifo)
        ac.os.open = probe_open
        dog = threading.Thread(target=watchdog, daemon=True)
        dog.start()
        try:
            out = expect("T32 nested target swapped for a FIFO with no reader after the write-side walk", root,
                         False, 2, targets=nested_target, unchanged=False)
        finally:
            ac.os.open = real_open
            finished.set()
            ready.set()
            dog.join()
            ac._target_dir_fd = real_walk
            for rescue_fd in rescuers:
                os.close(rescue_fd)
        if not fired:
            failures.append("T32: the write-leg swap hook did not run")
        if len(probes) != 1:
            failures.append("T32: expected one write-probe open of the FIFO, saw {}".format(probes))
        elif probes[0] == ("opened", True):
            failures.append("T32: the write probe opened the FIFO in blocking mode, so with no reader it "
                            "waits for one ({})".format("the watchdog supplied a reader" if rescuers
                                                        else "a reader was present"))
        elif probes[0][0] == "raised" and probes[0][1] != errno.ENXIO:
            failures.append("T32: the write probe failed with errno {}, not ENXIO".format(probes[0][1]))
        if nested_refusal not in out:
            failures.append("T32: the refusal is not the engine's, naming {}: {}".format(nested, out.strip()))
        if not stat.S_ISFIFO(os.lstat(root / nested).st_mode) or nested_strays(root):
            failures.append("T32: the FIFO was replaced or a temporary file was left: {}".format(
                nested_strays(root)))

        # T33. Another process replaces the temporary file after its creation, then the rename fails:
        #      exit 2, the target unchanged, and the foreign file at that name kept (a cleanup by name
        #      would delete it), the refusal naming the name it left.
        root = tree(ac.registry_text(), blocks=(), agents=legacy_golden)
        (root / ".aiqt" / "core" / "rules" / "r1.md").write_text(new_rule, encoding="utf-8")
        foreign = []

        def foreign_then_fail(src, _dst, **_k):
            (root / src).unlink()
            with open(root / src, "xb") as fh:
                fh.write(b"FOREIGN\n")
            foreign.append(src)
            raise OSError("injected rename failure")

        ac.os.replace = foreign_then_fail
        try:
            out = expect("T33 temporary name replaced after creation, then a failed rename", root, False, 2)
        finally:
            ac.os.replace = real_replace
        if len(foreign) != 1:
            failures.append("T33: the replacing hook did not run")
        else:
            if read(root / foreign[0]) != b"FOREIGN\n":
                failures.append("T33: the foreign file at the temporary name was deleted or rewritten")
            if foreign[0] not in out:
                failures.append("T33: the refusal does not name the file it left: {}".format(out.strip()))

        # T35. An exception other than OSError raised inside the try around the write and the rename,
        #      here a BaseException standing in for an interrupt and raised at the rename, propagates with
        #      a note naming the temporary file it left; the target is unchanged and the file is left at
        #      that name. Without the note nothing names the leftover. The target is nested, so the note
        #      must give the path from the root: the bare temporary name differs from it here.
        root = tree(ac.registry_text(), blocks=())
        (root / ".github").mkdir()
        (root / nested).write_bytes(b"old line\n")

        class Interrupted(BaseException):
            pass

        interrupted = []

        def interrupt_rename(src, _dst, **_k):
            interrupted.append(src)
            raise Interrupted()

        notes = None
        ac.os.replace = interrupt_rename
        try:
            ac._write_target(root, nested, b"new line\n")
        except Interrupted as exc:
            notes = getattr(exc, "__notes__", [])
        except Exception as exc:  # a refusal before the rename; reported here and by the check below
            failures.append("T35: the write raised {}: {}".format(type(exc).__name__, exc))
        finally:
            ac.os.replace = real_replace
        if len(interrupted) != 1 or notes is None:
            failures.append("T35: the injected interrupt did not reach the rename")
        else:
            left_rel = ".github/" + interrupted[0]
            if not any(left_rel in note for note in notes):
                failures.append("T35: the interrupt carries no note naming the temporary file {}: {}".format(
                    left_rel, notes))
            if read(root / left_rel) != b"new line\n":
                failures.append("T35: the temporary file was not left at the name the note gives")
        if read(root / nested) != b"old line\n":
            failures.append("T35: the interrupted write changed the target")

        # T36. A nested target swapped for a directory after the write-side walk: the probe's write-only
        #      open fails with EISDIR, refused (exit 2) in the engine's wording naming the path from the
        #      root, and the directory is not replaced.
        root = tree(ac.registry_text(), blocks=())
        (root / ".github").mkdir()
        (root / nested).write_bytes(b"old line\n")

        def to_dir():
            (root / nested).unlink()
            (root / nested).mkdir()

        ac._target_dir_fd, fired = write_leg_swap(to_dir)
        try:
            out = expect("T36 nested target swapped for a directory after the write-side walk", root, False, 2,
                         targets=nested_target, unchanged=False)
        finally:
            ac._target_dir_fd = real_walk
        if not fired:
            failures.append("T36: the write-leg swap hook did not run")
        if nested_refusal not in out:
            failures.append("T36: the refusal is not the engine's, naming {}: {}".format(nested, out.strip()))
        if not (root / nested).is_dir() or nested_strays(root):
            failures.append("T36: the directory was replaced or a temporary file was left: {}".format(
                nested_strays(root)))

        # T37 and T38 swap a nested target on the READ leg: the hook runs after the _target_dir_fd call
        # that _read_target makes (make_dirs=False), so the walk has judged a regular file and only the
        # read leg's open and fstat stand between the swap and the bytes read.
        def read_leg_swap(swap):
            fired = []

            def walk(*a, **k):
                result = real_walk(*a, **k)
                if not k.get("make_dirs") and not fired:
                    fired.append(True)
                    swap()
                return result
            return walk, fired

        # T37. A nested target swapped for a directory after the read-side walk: an O_RDONLY open admits a
        #      directory, and the fstat on that descriptor, made before any file object, refuses it (exit 2
        #      in both modes) in the engine's wording naming the path from the root; a file object made
        #      first raises IsADirectoryError naming the descriptor number instead.
        for check in (True, False):
            root = tree(ac.registry_text(), blocks=())
            (root / ".github").mkdir()
            (root / nested).write_bytes(b"old line\n")

            def to_dir_on_read():
                (root / nested).unlink()
                (root / nested).mkdir()

            ac._target_dir_fd, fired = read_leg_swap(to_dir_on_read)
            try:
                out = expect("T37 nested target swapped for a directory after the read-side walk", root, check,
                             2, targets=nested_target, unchanged=False)
            finally:
                ac._target_dir_fd = real_walk
            if not fired:
                failures.append("T37: the read-leg swap hook did not run")
            if nested_refusal + " (a directory)" not in out:
                failures.append("T37: the refusal is not the engine's, naming {}: {}".format(nested, out.strip()))
            if not (root / nested).is_dir() or nested_strays(root):
                failures.append("T37: the directory was replaced or a temporary file was left: {}".format(
                    nested_strays(root)))

        # T38. A nested target swapped for a UNIX socket after the read-side walk: the read leg's open fails
        #      with ENXIO, refused (exit 2 in both modes) in the engine's wording naming the path from the
        #      root, never the raw errno text naming the bare file name. The read leg's open of the final
        #      name is observed through a wrapper; where this environment cannot bind the socket, or answers
        #      its open with another errno (a sandbox can refuse it with EPERM), the wrapper raises ENXIO in
        #      its place, so the engine's mapping is judged either way.
        for check in (True, False):
            root = tree(ac.registry_text(), blocks=())
            (root / ".github").mkdir()
            (root / nested).write_bytes(b"old line\n")
            sockets, bound, read_opens = [], [], []

            def to_socket():
                (root / nested).unlink()
                try:
                    sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                except OSError:
                    return
                sockets.append(sk)
                here = os.getcwd()
                os.chdir(root / ".github")  # a relative bind stays under the AF_UNIX path length limit
                try:
                    sk.bind(nested_name)
                    bound.append(True)
                except OSError:
                    pass
                finally:
                    os.chdir(here)

            def read_open(path, flags, *a, **k):
                if (path != nested_name or flags & os.O_DIRECTORY
                        or flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR) != os.O_RDONLY):
                    return real_open(path, flags, *a, **k)
                read_opens.append(path)
                if not bound:
                    raise OSError(errno.ENXIO, os.strerror(errno.ENXIO), path)
                try:
                    return real_open(path, flags, *a, **k)
                except OSError as exc:
                    if exc.errno != errno.ENXIO:
                        raise OSError(errno.ENXIO, os.strerror(errno.ENXIO), path) from None
                    raise

            ac._target_dir_fd, fired = read_leg_swap(to_socket)
            ac.os.open = read_open
            try:
                out = expect("T38 nested target swapped for a socket after the read-side walk", root, check, 2,
                             targets=nested_target, unchanged=False)
            finally:
                ac.os.open = real_open
                ac._target_dir_fd = real_walk
                for sk in sockets:
                    sk.close()
            if not fired:
                failures.append("T38: the read-leg swap hook did not run")
            if len(read_opens) != 1:
                failures.append("T38: expected one read-leg open of the final name, saw {}".format(read_opens))
            if nested_refusal + " (a FIFO with no reader, a socket, or a device" not in out:
                failures.append("T38: the refusal is not the engine's, naming {}: {}".format(nested, out.strip()))
            if (root / nested).is_file() or nested_strays(root):
                failures.append("T38: the socket was replaced or a temporary file was left: {}".format(
                    nested_strays(root)))

        # T34. Descriptor accounting over every other vector (T1-T33, T35 to T38).
        fds_after = open_fds()
        if fds_before is not None and fds_after != fds_before:
            failures.append("T34: descriptors leaked across the matrix: {} open before, {} after".format(
                len(fds_before), len(fds_after or ())))
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
          "every registry and block-source error is exit 2, a wrong-typed registry field included, naming the "
          "field; CRLF is drift; either refused adapter blocks the other's write; no corpus is exit 2 and "
          "deletes nothing; an unreachable corpus deletes nothing; a marker-like rule body is exit 2 for a "
          "composed target; a symlinked target or parent, a dangling link and a directory target are exit 2 "
          "with nothing outside the tree touched; an in-sync legacy target rewrites; a write keeps the "
          "ordinary permission bits, dropping set-ID and sticky bits (under a pinned umask), and leaves no "
          "temporary file, and a failed rename names the one it "
          "leaves; a parent swapped for a symlink after the walk redirects neither a write nor a read; a "
          "symlink or a FIFO swapped in at the target is refused, as is a symlink planted against mkdir; "
          "a colliding "
          "temporary name is retried and the colliding file kept; a read-only target is refused; a "
          "platform without dir_fd support is refused; a symlink or a FIFO (held open or not) swapped in "
          "at the target after the write-side walk is refused by the write probe without blocking, as is a "
          "directory, each in the engine's wording naming the path from the root, and so is a directory or "
          "a socket swapped in after the read-side walk; a failed write leaves its temporary file, never "
          "deleting a foreign file at that name, and an interrupt at the rename carries a note naming it "
          "by its path from the root; and no descriptor is left open.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
