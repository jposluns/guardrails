#!/usr/bin/env python3
"""Generate the .cursor/rules/ Cursor adapter tree from .aiqt/core/rules/ sources.

Same core that feeds Claude's .claude/rules/ tree, so a Cursor session gets identical AIQT governance.
Cursor loads *.mdc files under .cursor/rules/ (recursing into subdirectories; a plain .md there is
ignored), and an .mdc whose frontmatter says `alwaysApply: true` is always included, with `globs` and
`description` ignored, so each rule becomes its own .mdc carrying that one key and the body of the
rule's full render (gen_rules.full_body) verbatim (H1 intact: each .mdc is its own document, unlike
the AGENTS.md/GEMINI.md concatenations, which demote it). All output lives under the RESERVED subtree
.cursor/rules/aiqt-guardrails/, mirroring the two-axis taxonomy, so reconciliation (including orphan
deletion) only ever touches the pack's own subtree and never an adopter's own Cursor rules beside it.
Format per
https://cursor.com/docs/rules (retrieved 2026-08-17).
  gen_cursor.py           regenerate .cursor/rules/aiqt-guardrails/{aiqt,security}/
  gen_cursor.py --check   fail (exit 1) on drift; exit 2 on a malformed source or a read/write failure
  gen_cursor.py --self-test  assert an invalid-UTF-8 generated .mdc target, a source path that is not a
                             directory or a symlink to a directory inside the root, and a reserved subtree, a
                             family directory inside it or a .cursor/ above it symlinked outside the root fail
                             closed (exit 2), and that a source using the `## Detail` split generates the
                             .mdc of its full render (gen_rules.full_body: core render, then detail
                             layer), byte for byte
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_cursor.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, unknown_args_refused  # noqa: E402
from _standards import dir_chain_or_absent, dir_or_absent, dir_present  # noqa: E402
from gen_rules import full_body, load_corpus, read_rule_source  # noqa: E402

# The reserved output subtree (relative parts, joined under repo root). Everything under it is
# generated; nothing outside it is ever written or deleted, so an adopter's own .cursor/rules files
# are structurally out of reach of the orphan prune.
OUT_PARTS = (".cursor", "rules", "aiqt-guardrails")
# The one frontmatter key an always-apply Cursor rule needs. globs and description are ignored under
# alwaysApply: true (cursor.com/docs/rules), so neither is emitted as a dead key.
FRONTMATTER = "---\nalwaysApply: true\n---\n\n"

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py; VER-CORE 6.5).
RENDERER_DECL = {"renderer-id": "cursor", "semantics-revision": 1}
GENSRC_OUTPUTS = (
    {"target": ".cursor/rules/aiqt-guardrails/", "kind": "tree",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_cursor.py"},
)


def cursor_rel(rel):
    """The Cursor tree path for a derived rule path: the same two-axis rel with the required .mdc
    suffix (a plain .md in .cursor/rules is ignored, so the suffix swap is load-bearing)."""
    return rel[:-len(".md")] + ".mdc"


def render_rule(path):
    """The full .mdc content for one source rule: the Cursor frontmatter, then the body of the rule's full
    render verbatim (gen_rules.full_body, the text gen_rules writes to the .claude/rules/ read tree, so a
    source using the `## Detail` split reads as its core render, which ends in the blank line or lines
    directly above the split, and its detail layer, the split line dropped; source frontmatter stripped,
    H1 kept;
    do NOT reuse gen_agents.body_of, which demotes the H1 for the single-document concatenations).
    conformance.py imports this so the checker and the generator can never disagree on the shape."""
    text = full_body(read_rule_source(path), path.name)
    end = text.find("\n---\n", 4)
    body = text[end + 5:].strip()
    return FRONTMATTER + body + "\n"


def run(root, check):
    """Reconcile the reserved .cursor/rules/aiqt-guardrails/ subtree under root against the
    .aiqt/core/rules/ corpus. Exit 0 in sync, 1 on drift (check mode), 2 on a malformed source or a
    read/write failure. Parameterized on root (rather than calling repo_root() inline) so the self-test
    can drive it against a synthetic tempdir tree, never the real repo."""
    src_dir = root / ".aiqt" / "core" / "rules"
    out_dir = root.joinpath(*OUT_PARTS)
    desired = {}
    try:
        # dir_or_absent (not is_dir) inside the try: an unreadable .aiqt/ parent, or a source path that is
        # there but cannot be read as a directory, must fail closed as exit 2, not read as an absent corpus
        # (which would delete every generated .mdc as an orphan below). A symlink to a directory inside the
        # root is read.
        # Then dir_present, bound beneath root, judges every component above it the same way, so a
        # .aiqt that is dangling, not a directory or a symlink resolving outside the root fails closed
        # too, never read as an absent corpus.
        _present, problem = dir_or_absent(src_dir, root)
        if problem is not None:
            print("error: {} {} (cannot evaluate)".format(src_dir, problem))
            return 2
        if dir_present(src_dir, root=root):
            for src, _fm, rel in load_corpus(src_dir):
                desired[cursor_rel(rel)] = render_rule(src)
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
        # dir_chain_or_absent (not is_dir, dir_present or dir_or_absent), before the first read, write or delete
        # in the reserved subtree: the subtree and the directory holding each generated .mdc are judged at every
        # component under the root (.cursor/ and .cursor/rules/ too), so one at any depth that is there but
        # cannot be read as a directory (a regular file, or a symlink that is dangling, whose target is not a
        # directory or resolves outside the root) is exit 2 with nothing written or deleted, never followed out
        # of the repository, and an unreadable parent raises (exit 2 below), never skipping the orphan scan. A
        # symlink to a directory inside the root is read. A directory absent here can only be created below,
        # holding desired files alone. The orphan scan deletes only in the directories os.walk enters below the
        # probed subtree, and os.walk does not follow a symlink there.
        probed = {}
        for path in (out_dir, *sorted({(out_dir / rel).parent for rel in desired})):
            probed[path], problem = dir_chain_or_absent(path, root)
            if problem is not None:
                print("error: {} {} (cannot evaluate)".format(path, problem))
                return 2
        for rel, content in sorted(desired.items()):
            target = out_dir / rel
            current = target.read_text(encoding="utf-8") if target.exists() else None
            if current != content:
                drift.append(rel)
                if not check:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
        # Orphan scan, scoped to the reserved subtree ONLY (it is 100% generated; siblings under
        # .cursor/rules/ may be the adopter's own and are never walked).
        if probed[out_dir]:
            for dirpath, _dirs, filenames in os.walk(out_dir, onerror=_raise):
                for fn in sorted(f for f in filenames if f.endswith(".mdc")):
                    f = Path(dirpath) / fn
                    # as_posix(), not str(): desired keys are forward-slash derive() paths, so a
                    # backslash from str() on Windows would flag every generated file as an orphan.
                    rel = f.relative_to(out_dir).as_posix()
                    if rel not in desired:
                        drift.append("orphan " + rel)
                        if not check:
                            f.unlink()
    except (OSError, UnicodeError) as exc:
        # UnicodeError (UnicodeDecodeError) covers the generated-TARGET read above: a non-UTF-8 .mdc
        # target decodes as UTF-8 there, so a corrupt target fails closed (exit 2) rather than a raw
        # traceback, the same OSError path (a read-only fs, a permission error, a full disk) already
        # fails closed on.
        print("error: {}".format(exc))
        return 2
    if check and drift:
        print("drift: " + "; ".join(drift))
        print("run tools/gen_cursor.py to regenerate")
        return 1
    return 0


def main():
    argv = sys.argv[1:]
    # Unknown-option rejection precedes every dispatch: main() defines only --check and --self-test, so any other
    # token (a misspelled --chek, a stray --help, a positional) is exit 2 with the usage line on stderr, never a
    # fall-through to a write-mode run.
    if unknown_args_refused(argv, "gen_cursor.py [--check | --self-test]"):
        return 2
    if "--self-test" in argv:
        return self_test_main()
    return run(repo_root(), "--check" in argv)


# --- self-test ----------------------------------------------------------------------------------------
# One focused invariant (the sibling generators' idiom): an invalid-UTF-8 GENERATED .mdc TARGET fails
# closed (exit 2) rather than a raw UnicodeDecodeError traceback. The reconcile loop reads each desired
# target as UTF-8 (the drift compare), so a non-UTF-8 target must be caught by the widened (OSError,
# UnicodeError) arm. A revert to the narrow OSError-only arm makes run() RAISE instead of returning 2,
# so this case fails and guards the widening. Tempdir-only; never touches a real repo file.

_RULE_SRC = """---
corpus-id: selfc1
origin: pack
family: aiqt
tier: 10
facet: QUALI
slug: gen-cursor-selftest-target
---
# Gen-cursor self-test rule

A minimal rule so the reconcile has one desired .mdc target to read.
"""
_RULE_MDC_REL = "aiqt/10-QUALI-gen-cursor-selftest-target.mdc"


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def run_quiet(root, check):
        # A reverted narrow (OSError-only) arm raises UnicodeDecodeError out of run(); catch it and
        # return a non-int sentinel so it registers as a FAILURE against the expected exit code rather
        # than aborting the self-test or letting it exit early green.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except Exception as exc:  # noqa: BLE001  a revert surfaces here as UnicodeDecodeError
                return "raised {}".format(type(exc).__name__)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-cursor-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    try:
        # A synthetic corpus (one source rule) so `desired` carries exactly one generated .mdc target,
        # then pre-write that target as invalid UTF-8 bytes so the reconcile's drift-compare read hits it.
        src = tmp / ".aiqt" / "core" / "rules"
        src.mkdir(parents=True)
        (src / "gen-cursor-selftest-target.md").write_text(_RULE_SRC, encoding="utf-8")
        target = tmp.joinpath(*OUT_PARTS) / _RULE_MDC_REL
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\xff\xfe not utf-8")
        if run_quiet(tmp, check=True) != 2:
            failures.append("invalid-UTF-8 generated .mdc target expected exit 2 (fail-closed)")
        # A source path that is there but is not a directory is exit 2 in write mode with the generated
        # .mdc kept, never read as an absent corpus that prunes every generated .mdc as an orphan.
        nondir = tmp / "nondir"
        kept = nondir.joinpath(*OUT_PARTS) / _RULE_MDC_REL
        kept.parent.mkdir(parents=True)
        kept.write_text("kept\n", encoding="utf-8")
        (nondir / ".aiqt" / "core").mkdir(parents=True)
        (nondir / ".aiqt" / "core" / "rules").write_text("not a directory\n", encoding="utf-8")
        if run_quiet(nondir, check=False) != 2 or not kept.exists():
            failures.append("a source path that is a regular file expected exit 2 with the generated "
                            ".mdc kept")
        # A source path that is a symlink, in write mode: to a directory inside the root holding the rule, it is
        # read (exit 0, the generated .mdc rewritten from it); dangling, to a regular file inside the root, or
        # to an empty directory outside the root, it is exit 2 with the generated .mdc kept as it was, never
        # read as an absent corpus or followed to one that prunes it as an orphan.
        (tmp / "outside").mkdir()
        for shape, want in (("in-root", 0), ("dangling", 2), ("to-file", 2), ("outside", 2)):
            linked = tmp / ("linked-" + shape)
            kept = linked.joinpath(*OUT_PARTS) / _RULE_MDC_REL
            kept.parent.mkdir(parents=True)
            kept.write_text("kept\n", encoding="utf-8")
            (linked / "shared" / "rules").mkdir(parents=True)
            (linked / "shared" / "rules" / "gen-cursor-selftest-target.md").write_text(_RULE_SRC, encoding="utf-8")
            (linked / "shared" / "file.txt").write_text("not a directory\n", encoding="utf-8")
            (linked / ".aiqt" / "core").mkdir(parents=True)
            target = {"in-root": Path("..", "..", "shared", "rules"), "dangling": Path("absent"),
                      "to-file": Path("..", "..", "shared", "file.txt"), "outside": tmp / "outside"}[shape]
            (linked / ".aiqt" / "core" / "rules").symlink_to(target)
            got = run_quiet(linked, check=False)
            rewritten = kept.exists() and kept.read_text(encoding="utf-8") != "kept\n"
            if got != want or not kept.exists() or rewritten != (shape == "in-root"):
                failures.append("a source path that is a symlink {} expected exit {} with the generated .mdc "
                                "{}, got {}".format(shape, want, "rewritten" if want == 0 else "kept", got))
        # The reserved subtree that is a symlink to a directory holding a planted orphan .mdc and lacking the
        # generated one, in write mode: outside the root, it is exit 2 with the orphan kept and the .mdc not
        # written there, never followed to prune or write out of the repository; inside the root, it is read
        # (exit 0, the orphan pruned and the .mdc written there).
        for where, want in (("outside", 2), ("in-root", 0)):
            out = tmp / ("out-" + where)
            (out / ".aiqt" / "core" / "rules").mkdir(parents=True)
            (out / ".aiqt" / "core" / "rules" / "gen-cursor-selftest-target.md").write_text(_RULE_SRC, encoding="utf-8")
            link = out.joinpath(*OUT_PARTS)
            link.parent.mkdir(parents=True)
            held = tmp / "held-outside" if where == "outside" else out / "shared" / "held"
            (held / "aiqt").mkdir(parents=True)
            (held / "aiqt" / "zz-planted-orphan.mdc").write_text("planted\n", encoding="utf-8")
            link.symlink_to(held if where == "outside" else os.path.relpath(held, link.parent))
            got = run_quiet(out, check=False)
            kept = (held / "aiqt" / "zz-planted-orphan.mdc").exists()
            wrote = (held / _RULE_MDC_REL).exists()
            if (got, kept, wrote) != (want, want == 2, want == 0):
                failures.append("a reserved subtree that is a symlink {} expected exit {} with the orphan {} there, "
                                "got exit {}, orphan kept {}, .mdc written {}".format(
                                    where, want, "kept and nothing written" if want == 2 else "pruned", got, kept,
                                    wrote))
        # A directory deeper or higher than the reserved subtree that is a symlink to a directory holding a planted
        # orphan .mdc and lacking the generated one, in write mode: the aiqt family directory inside the subtree,
        # or the .cursor/ directory above it, with the rule source and, for .cursor/, also with no corpus (nothing
        # desired, so only the subtree's own probe stands before the orphan scan). Outside the root, it is exit 2
        # with the orphan kept and the .mdc not written there, never followed to write or prune out of the
        # repository; inside the root, it is read (exit 0, any desired .mdc written there, and below a linked
        # .cursor/ the orphan pruned; os.walk does not enter a linked family directory, so that orphan is not
        # judged here).
        for parts, sub, source in (((".cursor",), ("rules", "aiqt-guardrails", "aiqt"), True),
                                   ((".cursor",), ("rules", "aiqt-guardrails", "aiqt"), False),
                                   (OUT_PARTS + ("aiqt",), (), True)):
            for where, want in (("outside", 2), ("in-root", 0)):
                out = tmp / ("deep-" + parts[-1].lstrip(".") + ("" if source else "-nosource") + "-" + where)
                out.mkdir()
                if source:
                    (out / ".aiqt" / "core" / "rules").mkdir(parents=True)
                    (out / ".aiqt" / "core" / "rules" / "gen-cursor-selftest-target.md").write_text(
                        _RULE_SRC, encoding="utf-8")
                link = out.joinpath(*parts)
                link.parent.mkdir(parents=True, exist_ok=True)
                held = tmp / ("held-" + out.name) if where == "outside" else out / "shared" / "held"
                fam = held.joinpath(*sub)
                fam.mkdir(parents=True)
                (fam / "zz-planted-orphan.mdc").write_text("planted\n", encoding="utf-8")
                link.symlink_to(held if where == "outside" else os.path.relpath(held, link.parent))
                got = run_quiet(out, check=False)
                kept = (fam / "zz-planted-orphan.mdc").exists()
                wrote = (fam / Path(_RULE_MDC_REL).name).exists()
                judged = want == 2 or parts == (".cursor",)
                if (got, wrote) != (want, want == 0 and source) or (judged and kept != (want == 2)):
                    failures.append("a {} that is a symlink {}{} expected exit {} with the .mdc {} there{}, got "
                                    "exit {}, orphan kept {}, .mdc written {}".format(
                                        "/".join(parts), where, "" if source else " with no corpus", want,
                                        "written" if want == 0 and source else "not written",
                                        (" and the orphan " + ("kept" if want == 2 else "pruned")) if judged else "",
                                        got, kept, wrote))
        # A rule source using the `## Detail` split renders as its full render (gen_rules.full_body: core render,
        # a blank line, detail layer): for every gen_rules _SPLIT_CASES pair, the .mdc tree generated from the
        # split source is byte-equal to the one generated from its expected full render, and --check over it is
        # clean. Red when render_rule reads the source text instead of its full render (the split line would
        # leak into the .mdc), and red under the previous full_body, which dropped the core layer.
        from gen_rules import _SPLIT_CASES, _SPLIT_NAME, _split_case_texts
        for case in _SPLIT_CASES:
            trees = []
            for label, text in zip(("original", "split"), _split_case_texts(case)):
                case_root = tmp / "split" / case[0] / label
                (case_root / ".aiqt" / "core" / "rules").mkdir(parents=True)
                (case_root / ".aiqt" / "core" / "rules" / _SPLIT_NAME).write_bytes(text.encode("utf-8"))
                codes = (run_quiet(case_root, check=False), run_quiet(case_root, check=True))
                if codes != (0, 0):
                    failures.append("split case {} ({}): write then --check expected exits (0, 0), got {!r}"
                                    .format(case[0], label, codes))
                out = case_root.joinpath(*OUT_PARTS)
                trees.append({f.relative_to(out).as_posix(): f.read_bytes()
                              for f in sorted(out.rglob("*.mdc"))} if out.is_dir() else {})
            if not trees[0] or trees[1] != trees[0]:
                failures.append("split case {}: the .mdc tree from the split source is not byte-equal to the one "
                                "from its expected full render; got {!r}".format(
                                    case[0], [v[-300:] for v in trees[1].values()]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # An unknown option is exit 2 with the usage line and runs nothing (red when main()'s guard is reverted):
    # gen_rules._argv_guard_failures drives main() with run and repo_root stood in for, touching no file.
    from gen_rules import _argv_guard_failures
    failures.extend(_argv_guard_failures(globals(), "gen_cursor.py"))

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: an invalid-UTF-8 generated .mdc target fails closed (exit 2), not a raw "
          "UnicodeDecodeError traceback (guards the widened reconcile arm); a source path that is a "
          "regular file or a symlink that is dangling, to a regular file or to a directory outside the root "
          "exits 2 with the generated .mdc kept, and one symlinked to a directory inside the root is read; a "
          "reserved subtree, a family directory inside it or a .cursor/ directory above it symlinked outside the "
          "root exits 2 in write mode with nothing written or deleted there, and one symlinked inside the root is "
          "read; a rule source using the `## Detail` split generates an .mdc tree byte-equal to its expected "
          "full render's, core then detail ({} split cases); an unknown option is exit 2 with the usage line and runs nothing."
          .format(len(_SPLIT_CASES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
