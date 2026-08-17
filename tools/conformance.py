#!/usr/bin/env python3
"""Adopter conformance suite for an installed AIQT Guardrails pack (smallest-first slice).

An adopter runs this against their own installed pack to check that what is on disk still conforms:
the core corpus parses, every generated surface matches regeneration, the taxonomy placement holds,
and every cited standards id is real. It is a THIN ORCHESTRATOR over the repo's parameterized
validation functions, re-orchestrated under --root. Offline, stdlib only, fail-closed.

  conformance.py [--root DIR]   check the install rooted at DIR (default: cwd)
  conformance.py --self-test    build synthetic trees and assert the suite's own honesty invariants

Exit convention (matches the repo's gates):
  0  clean, including a clean run where checks were NOT APPLICABLE
  1  a real conformance finding (drift, placement violation, mapping id not in its manifest)
  2  malformed input or a read error (fail-closed)

Design of record (see the GA-2 SYNTHESIS, sections 4 and 6, divergence D0): this suite reuses the
repo's PARAMETERIZED validation functions, re-orchestrated under --root. It never calls the
repo-anchored main()s (gen_rules.main / gen_agents.main / check_mappings.main are all anchored to
repo_root() with no --root override), because those reconcile the repo the tool physically sits in,
not an arbitrary --root. It reuses: gen_rules.load_corpus / gen_rules.derive, gen_agents.render /
sort_key, check_rule_placement.check_name / check_drift, and _standards.load_manifests plus the
mapping-validation loop.

The allowed set of `map-*` frontmatter keys and the standards ids are the ADOPTER's, not this repo's:
gen_rules binds MAP_KEYS/SEQ_KEYS at import from ITS OWN repo_root(), so before any load_corpus/derive
this suite REBINDS them from the --root install's own .aiqt/standards/ (see _rebind_map_keys). Without
that rebind, an adopter whose standards differ from the checker's would have their map-keys judged
against the wrong set.

Where an input an adopter lacks is absent, the check degrades to NOT APPLICABLE and the run continues;
nothing fakes a pass, and a check that validated nothing reports NOT APPLICABLE rather than a hollow
PASS. Behaviour is never scored: it prints NOT PROVEN.
"""
import os
import sys
from pathlib import Path

# Sibling-import idiom, identical to check_rule_placement.py / check_mappings.py: this file lives in
# tools/ next to the modules it reuses.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_rules            # noqa: E402  load_corpus, derive, MAP_KEYS, SEQ_KEYS
import gen_agents           # noqa: E402  render, sort_key, body_of
import gen_adapters         # noqa: E402  ADAPTERS, render (GEMINI.md / copilot-instructions.md)
import gen_cursor           # noqa: E402  render_rule, cursor_rel, OUT_PARTS (Cursor .mdc tree)
import gen_hooks            # noqa: E402  build_desired, PLUGIN_ROOT_PARTS, HOOKS_SUBTREE_PARTS (hooks plugin)
import check_rule_placement as crp  # noqa: E402  check_name, check_drift, METADATA, BUILTIN_FAMILIES
from _standards import dir_present, load_manifests, map_keys, validate_mappings, ManifestError  # noqa: E402


# Status constants for a single check's outcome.
PASS = "PASS"
FAIL = "FAIL"                 # a real finding -> exit 1
NA = "NOT APPLICABLE"         # input absent / nothing to validate -> degrade, never fake a pass
MALFORMED = "MALFORMED"       # unparseable input / read error -> exit 2 (fail-closed)
SKIPPED = "SKIPPED"           # a dependency check already failed; not scored as PASS

BEHAVIOUR_DOC = ".aiqt/core/conformance/behaviour-checklist.md"


class Result:
    """One check's outcome: an id, a human label, a status, and a detail line."""
    __slots__ = ("cid", "label", "status", "detail")

    def __init__(self, cid, label, status, detail=""):
        self.cid = cid
        self.label = label
        self.status = status
        self.detail = detail


def _walk_raise(exc):
    """os.walk onerror callback that re-raises, so an unreadable dir at any depth fails closed instead
    of being silently skipped (os.walk, like rglob, ignores traversal errors by default)."""
    raise exc


def _corpus_src(root):
    return root / ".aiqt" / "core" / "rules"


def _standards_dir(root):
    return root / ".aiqt" / "standards"


def _reachable(path):
    """Fail-closed replacement for the `if not X.is_dir()` absence guards. Returns (present, error):
    (True/False, None) when the path is reachable (present dir / genuinely absent), or (None, msg) when
    it EXISTS but cannot be read (an unreadable dir or an unreadable .aiqt/.claude/ parent). is_dir()
    swallows EACCES and returns False, so an unreadable input would read as absent -> NOT APPLICABLE (a
    false-clean); dir_present raises on EACCES so the caller can render it as MALFORMED (exit 2) in its
    own Result/tuple shape."""
    try:
        return (dir_present(path), None)
    except OSError as exc:
        return (None, "cannot read {}: {}".format(path, exc))


def _rebind_map_keys(root):
    """Rebind gen_rules.MAP_KEYS / SEQ_KEYS from the --root install's own .aiqt/standards/.

    gen_rules binds these at import from its OWN repo_root(); load_corpus / derive validate which
    `map-*` frontmatter keys a rule may carry against them. Run against an adopter --root whose
    standards differ, that import-time set is wrong. Rebinding here makes the whole suite --root-aware:
    an adopter's map-keys are judged against the adopter's manifests. Cheap (map_keys does not parse)."""
    gen_rules.MAP_KEYS = map_keys(root)
    gen_rules.SEQ_KEYS = {"secondary"} | gen_rules.MAP_KEYS


# --- C1: corpus loads -------------------------------------------------------------------------------

def check_c1(root, cache):
    """.aiqt/core/rules/*.md parse, schema-validate, and have unique corpus-id and derived path.

    Reuses gen_rules.load_corpus verbatim (fully parameterized on src_dir). Caches the loaded corpus
    for C2 and C4. Absent core -> NOT APPLICABLE (a read-tree-only install has no core to load); a
    present-but-empty core (a dir with no rule sources) is also NOT APPLICABLE, never a hollow pass."""
    src = _corpus_src(root)
    present, err = _reachable(src)
    if err:
        cache["corpus_error"] = True
        return Result("C1", "corpus-loads", MALFORMED, err)
    if not present:
        return Result("C1", "corpus-loads", NA, "no .aiqt/core/rules/ installed")
    try:
        cache["corpus"] = gen_rules.load_corpus(src)
    except (ValueError, OSError) as exc:
        cache["corpus_error"] = True
        return Result("C1", "corpus-loads", MALFORMED, str(exc))
    n = len(cache["corpus"])
    if n == 0:
        return Result("C1", "corpus-loads", NA, ".aiqt/core/rules/ holds no rule sources to load")
    return Result("C1", "corpus-loads", PASS, "{} rule(s) parse and validate".format(n))


# --- C2: no drift -----------------------------------------------------------------------------------

def _claude_drift(root, corpus):
    """Mirror gen_rules.main()'s reconciliation, re-rooted at root (never call that main()).

    Returns (status, detail). Absent .claude/rules/ surface -> NOT APPLICABLE (the adopter did not
    install the Claude surface). Present-but-different or orphaned generated files -> FAIL."""
    claude_dir = root / ".claude" / "rules"
    present, err = _reachable(claude_dir)
    if err:
        return (MALFORMED, err)
    if not present:
        return (NA, "no .claude/rules/ surface installed")
    desired = {}
    try:
        for src, _fm, rel in corpus:
            desired[rel] = src.read_text(encoding="utf-8")
    except OSError as exc:
        return (MALFORMED, "cannot read a source rule: {}".format(exc))
    drift = []
    try:
        for rel, content in sorted(desired.items()):
            target = claude_dir / rel
            current = target.read_text(encoding="utf-8") if target.exists() else None
            if current != content:
                drift.append(rel)
        for family in ("aiqt", "security"):
            fam_dir = claude_dir / family
            if dir_present(fam_dir):  # not is_dir: an unreadable .claude parent must fail closed (MALFORMED), not skip the orphan scan
                # os.walk(onerror=raise), not rglob: rglob silently skips an unreadable dir at ANY depth
                # (concealing an orphan); os.walk surfaces the read error, caught below as MALFORMED.
                for dirpath, _dirs, filenames in os.walk(fam_dir, onerror=_walk_raise):
                    for fn in sorted(f for f in filenames if f.endswith(".md")):
                        f = Path(dirpath) / fn
                        # as_posix(), not str(): derive() emits forward-slash rel paths, so a backslash
                        # from str() on Windows would make every generated file read as an orphan.
                        rel = f.relative_to(claude_dir).as_posix()
                        if rel not in desired:
                            drift.append("orphan " + rel)
    except OSError as exc:
        return (MALFORMED, "cannot read the .claude/rules/ tree: {}".format(exc))
    if drift:
        return (FAIL, ".claude/rules drift: " + "; ".join(sorted(set(drift))))
    return (PASS, ".claude/rules matches regeneration")


def _agents_drift(root, corpus):
    """Mirror gen_agents.main()'s reconciliation, re-rooted at root. Absent AGENTS.md -> NOT APPLICABLE
    (the adopter did not install the Codex surface)."""
    out = root / "AGENTS.md"
    if not out.exists():
        return (NA, "no AGENTS.md surface installed")
    try:
        pairs = [(src, fm) for src, fm, _ in corpus]
        pairs.sort(key=lambda pf: gen_agents.sort_key(pf[1]))
        content = gen_agents.render(pairs)
        current = out.read_text(encoding="utf-8")
    except (ValueError, OSError) as exc:
        return (MALFORMED, "cannot render/read AGENTS.md: {}".format(exc))
    if current != content:
        return (FAIL, "AGENTS.md drift")
    return (PASS, "AGENTS.md matches regeneration")


def _adapter_drift(root, corpus, adapter):
    """Mirror gen_adapters for one adapter (GEMINI.md / .github/copilot-instructions.md), re-rooted at
    root. Absent file -> NOT APPLICABLE (the adopter did not install that assistant's surface)."""
    out = root.joinpath(*adapter["parts"])
    label = adapter["label"]
    parts = "/".join(adapter["parts"])
    # Probe by READING, not Path.exists(): on Python 3.12 exists() returns False on EACCES (an unreadable
    # .github/ parent for the nested copilot surface) rather than raising, which would mask a present-but-
    # unreadable surface as a false NOT APPLICABLE. read_text distinguishes FileNotFoundError (the adopter
    # did not install this surface -> NA) from any other OSError (present but unreadable -> MALFORMED,
    # fail closed), independent of the Python version.
    try:
        current = out.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (label, NA, "no {} surface installed".format(parts))
    except (ValueError, OSError) as exc:
        return (label, MALFORMED, "cannot read {}: {}".format(parts, exc))
    try:
        pairs = [(src, fm) for src, fm, _ in corpus]
        pairs.sort(key=lambda pf: gen_agents.sort_key(pf[1]))
        content = gen_adapters.render(pairs, adapter["header"])
    except (ValueError, OSError) as exc:
        return (label, MALFORMED, "cannot render {}: {}".format(parts, exc))
    if current != content:
        return (label, FAIL, "{} drift".format(parts))
    return (label, PASS, "{} matches regeneration".format(parts))


def _cursor_drift(root, corpus):
    """Mirror gen_cursor.main()'s reconciliation, re-rooted at root (never call that main()).

    A directory surface like .claude/rules, so the detail reports counts and rel-paths, not one
    file's status. Only the RESERVED subtree .cursor/rules/aiqt-guardrails/ is compared and orphan-
    scanned; an adopter's own rules beside it are never read. Absent subtree -> NOT APPLICABLE (the
    adopter did not install the Cursor surface)."""
    cursor_dir = root.joinpath(*gen_cursor.OUT_PARTS)
    present, err = _reachable(cursor_dir)
    if err:
        return (MALFORMED, err)
    if not present:
        return (NA, "no .cursor/rules/aiqt-guardrails/ surface installed")
    desired = {}
    try:
        for src, _fm, rel in corpus:
            desired[gen_cursor.cursor_rel(rel)] = gen_cursor.render_rule(src)
    except (ValueError, OSError) as exc:
        return (MALFORMED, "cannot render a Cursor rule: {}".format(exc))
    drift = []
    try:
        for rel, content in sorted(desired.items()):
            target = cursor_dir / rel
            current = target.read_text(encoding="utf-8") if target.exists() else None
            if current != content:
                drift.append(rel)
        for dirpath, _dirs, filenames in os.walk(cursor_dir, onerror=_walk_raise):
            for fn in sorted(f for f in filenames if f.endswith(".mdc")):
                f = Path(dirpath) / fn
                rel = f.relative_to(cursor_dir).as_posix()
                if rel not in desired:
                    drift.append("orphan " + rel)
    except OSError as exc:
        return (MALFORMED, "cannot read the .cursor/rules/aiqt-guardrails/ tree: {}".format(exc))
    if drift:
        return (FAIL, "{} of {} Cursor rule(s) drifted/orphaned: {}".format(
            len(set(drift)), len(desired), "; ".join(sorted(set(drift)))))
    return (PASS, "{} Cursor rule(s) match regeneration".format(len(desired)))


def check_c2(root, cache):
    """Each generated surface the adopter HAS matches regeneration from their .aiqt/core/rules/.

    Reuses load_corpus output (C1) + gen_agents.render/sort_key; re-orchestrates the drift comparison
    under --root rather than calling the repo-anchored gen_*.main(). No corpus -> NOT APPLICABLE."""
    if cache.get("corpus_error"):
        return Result("C2", "no-drift", SKIPPED, "corpus did not load (see C1)")
    # Gate on the core dir EXISTING, not on a non-empty corpus. An absent .aiqt/core/rules/ is a thin
    # read-only install with no sources to regenerate from (NOT APPLICABLE, honestly coverage-qualified).
    # But a PRESENT-yet-empty core with generated surfaces still on disk is a broken/partial install:
    # reconcile against the empty corpus so orphaned .claude/rules files and a stale AGENTS.md are caught
    # (an empty desired set makes every generated file an orphan), exactly as gen_rules.main() does. A
    # bare `not corpus` guard would mask that empty-core case as NOT APPLICABLE.
    present, err = _reachable(_corpus_src(root))
    if err:
        return Result("C2", "no-drift", MALFORMED, err)
    if not present:
        return Result("C2", "no-drift", NA, "no .aiqt/core/rules/ to regenerate from")
    corpus = cache.get("corpus", [])
    cs, cd = _claude_drift(root, corpus)
    as_, ad = _agents_drift(root, corpus)
    us, ud = _cursor_drift(root, corpus)
    surfaces = [("claude", cs, cd), ("agents", as_, ad), ("cursor", us, ud)]
    for adapter in gen_adapters.ADAPTERS:
        surfaces.append(_adapter_drift(root, corpus, adapter))
    # Aggregate every surface: worst status wins, but NA of one surface does not mask a PASS/FAIL of
    # another. Precedence MALFORMED > FAIL > PASS > NA.
    order = {MALFORMED: 3, FAIL: 2, PASS: 1, NA: 0}
    worst = max((st for _, st, _ in surfaces), key=lambda st: order[st])
    detail = "; ".join("{}: {} ({})".format(name, st, dt) for name, st, dt in surfaces)
    if worst == NA:
        return Result("C2", "no-drift", NA, "no generated surface installed to check")
    return Result("C2", "no-drift", worst, detail)


# --- C3: placement (the unconditional floor) --------------------------------------------------------

def check_c3(root):
    """The read tree obeys the taxonomy: family, flatness, naming, frontmatter-derived path, apex,
    provenance. Reuses check_rule_placement.check_name / check_drift and its module constants, and
    re-orchestrates the same walk main() does (there is no standalone walk function to import), so the
    orchestrator keeps control of reporting. Runs unconditionally: it needs only .claude/rules/, which
    every adopter has. An absent tree trivially conforms."""
    rules = root / ".claude" / "rules"
    present, err = _reachable(rules)
    if err:
        return Result("C3", "placement", MALFORMED, err)
    if not present:
        return Result("C3", "placement", NA, "no .claude/rules/ tree installed to validate")
    findings = []
    evaluated = 0  # rule files / vendored sources actually checked; 0 means nothing was validated

    def report(path, code, detail):
        findings.append("{}: {}: {}".format(path.relative_to(root), code, detail))

    try:
        for item in sorted(rules.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_file():
                if item.suffix == ".md" and item.name not in crp.METADATA:
                    report(item, "loose", "a rule file at the tree root, not on the metadata allowlist")
                continue
            family = item.name
            if family not in crp.BUILTIN_FAMILIES:
                report(item, "unknown-family", "not a registered family directory")
                continue
            if family == "external":
                for sub in sorted(p for p in item.iterdir() if p.is_dir()):
                    evaluated += 1
                    if not (sub / "PROVENANCE.md").exists() or not (sub / "LICENSE").exists():
                        report(sub, "missing-provenance", "a vendored source lacks PROVENANCE.md or LICENSE")
                continue
            for entry in sorted(item.iterdir()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    report(entry, "nested-dir", "a flat family admits no subdirectory")
                    continue
                if entry.suffix != ".md":
                    continue
                evaluated += 1
                nc = crp.check_name(family, entry.name)
                if nc:
                    report(entry, nc[0], nc[1])
                    continue
                drift = crp.check_drift(entry, rules)
                if drift:
                    report(entry, drift[0], drift[1])
    except OSError as exc:
        return Result("C3", "placement", MALFORMED, "cannot read the rules tree: {}".format(exc))
    if findings:
        return Result("C3", "placement", FAIL,
                      "{} violation(s): {}".format(len(findings), "; ".join(sorted(set(findings)))))
    # A tree that exists but holds no rule files validated nothing: report NOT APPLICABLE, never a
    # hollow PASS that the coverage line would count as "verified C3".
    if evaluated == 0:
        return Result("C3", "placement", NA, "no rule files under .claude/rules/ to validate")
    return Result("C3", "placement", PASS,
                  "rule placement conforms to the taxonomy ({} item(s) checked)".format(evaluated))


# --- C4: mappings (best-effort) ---------------------------------------------------------------------

def check_c4(root, cache):
    """Every map-* id a rule cites is real per its pinned manifest. Reuses _standards.load_manifests
    (parameterized on std_dir) + the check_mappings inner validation loop, over load_corpus output.
    Needs BOTH .aiqt/core/rules/ and .aiqt/standards/; either absent -> NOT APPLICABLE. A run that
    validated no id at all (no rule cites a mapping, or no manifest is installed) is NOT APPLICABLE
    rather than a hollow "0 ids validate" PASS, unless a real finding was raised."""
    if cache.get("corpus_error"):
        return Result("C4", "mappings", SKIPPED, "corpus did not load (see C1)")
    src = _corpus_src(root)
    std = _standards_dir(root)
    present, err = _reachable(std)
    if err:
        return Result("C4", "mappings", MALFORMED, err)
    if not present:
        return Result("C4", "mappings", NA, "no .aiqt/standards/ installed")
    # Parse the standards FIRST, fail-closed, BEFORE the absent-corpus short-circuit: a malformed or
    # unreadable manifest must surface as MALFORMED (exit 2), never hide behind an absent corpus and
    # read as clean. Catch OSError too (an unreadable manifest file is a read error, not a clean skip).
    try:
        manifests = load_manifests(std)
    except (ManifestError, OSError) as exc:
        return Result("C4", "mappings", MALFORMED, str(exc))
    present, err = _reachable(src)
    if err:
        return Result("C4", "mappings", MALFORMED, err)
    if not present:
        return Result("C4", "mappings", NA, "no .aiqt/core/rules/ installed to cite mappings")
    corpus = cache.get("corpus")
    if corpus is None:
        try:
            corpus = gen_rules.load_corpus(src)
        except (ValueError, OSError) as exc:
            return Result("C4", "mappings", MALFORMED, str(exc))

    findings, mapped, tight, broad = validate_mappings(corpus, manifests)
    if findings:
        return Result("C4", "mappings", FAIL,
                      "{} issue(s): {}".format(len(findings), "; ".join(sorted(findings))))
    if mapped == 0:
        return Result("C4", "mappings", NA,
                      "no rule cites a mapping id to validate ({} manifest(s) installed)".format(
                          len(manifests)))
    return Result("C4", "mappings", PASS,
                  "{} mapping id(s): {} tight, {} broad, validate against {} manifest(s)".format(
                      mapped, tight, broad, len(manifests)))


# --- C5: hooks-no-drift (the opt-in enforcement plugin) ---------------------------------------------

def check_c5(root, cache):
    """The installed hooks plugin surface matches regeneration from .aiqt/core/hooks/. Reuses
    gen_hooks.build_desired (parameterized on root; never gen_hooks.run(), which reconciles and WRITES,
    conformance only compares). NA-degrading, the opt-in-plugin analogue of C2's per-surface degrade:

      source absent AND surface absent   -> NOT APPLICABLE (the enforcement plugin is not installed)
      source present, surface absent     -> NOT APPLICABLE (the plugin is opt-in; the manifest alone
                                            obliges nothing)
      source absent, surface present     -> FAIL (an orphaned enforcement surface that can no longer be
                                            regenerated is stale enforcement)
      both present                       -> compare each generated file and orphan-scan the reserved
                                            plugin hooks/ subtree; any mismatch/orphan -> FAIL, else PASS

    Either tree unreadable -> MALFORMED (exit 2). corpus_error -> SKIPPED (build_desired needs the
    corpus for id validation), mirroring C2/C4."""
    if cache.get("corpus_error"):
        return Result("C5", "hooks-no-drift", SKIPPED, "corpus did not load (see C1)")
    src_dir = root / ".aiqt" / "core" / "hooks"
    plugin_root = root.joinpath(*gen_hooks.PLUGIN_ROOT_PARTS)
    src_present, src_err = _reachable(src_dir)
    if src_err:
        return Result("C5", "hooks-no-drift", MALFORMED, src_err)
    surf_present, surf_err = _reachable(plugin_root)
    if surf_err:
        return Result("C5", "hooks-no-drift", MALFORMED, surf_err)
    if not surf_present:
        if src_present:
            return Result("C5", "hooks-no-drift", NA,
                          "hooks surface not installed; the enforcement plugin is opt-in")
        return Result("C5", "hooks-no-drift", NA, "no hooks manifest or surface installed")
    if not src_present:
        return Result("C5", "hooks-no-drift", FAIL,
                      "a hooks plugin surface is installed but no .aiqt/core/hooks/ source remains to "
                      "regenerate it (orphaned, unmaintainable enforcement)")
    try:
        desired = gen_hooks.build_desired(root, src_dir)
    except (ValueError, OSError) as exc:
        return Result("C5", "hooks-no-drift", MALFORMED, str(exc))
    drift = []
    try:
        for rel, content in sorted(desired.items()):
            target = root / rel
            try:
                current = target.read_text(encoding="utf-8")
            except FileNotFoundError:
                drift.append("missing " + rel)
                continue
            if current != content:
                drift.append(rel)
        # Orphan scan the RESERVED plugin hooks/ subtree only (100% generated); the plugin.json is in
        # desired and compared above, and .claude-plugin/ is not otherwise walked.
        hooks_dir = root.joinpath(*gen_hooks.HOOKS_SUBTREE_PARTS)
        present, err = _reachable(hooks_dir)
        if err:
            return Result("C5", "hooks-no-drift", MALFORMED, err)
        if present:
            for dirpath, _dirs, filenames in os.walk(hooks_dir, onerror=_walk_raise):
                for fn in sorted(filenames):
                    f = Path(dirpath) / fn
                    rel = f.relative_to(root).as_posix()
                    if rel not in desired:
                        drift.append("orphan " + rel)
    except (OSError, ValueError) as exc:
        # ValueError catches UnicodeDecodeError from read_text on a non-utf-8 generated file; fail
        # closed as MALFORMED (exit 2), never a traceback.
        return Result("C5", "hooks-no-drift", MALFORMED,
                      "cannot read the hooks plugin surface: {}".format(exc))
    if drift:
        return Result("C5", "hooks-no-drift", FAIL,
                      "{} hook surface file(s) drifted/orphaned: {}".format(
                          len(set(drift)), "; ".join(sorted(set(drift)))))
    return Result("C5", "hooks-no-drift", PASS,
                  "{} hook surface file(s) match regeneration".format(len(desired)))


# --- orchestration ----------------------------------------------------------------------------------

def run(root):
    """Run every check under root, print a per-stage report and a coverage-qualified final line, and
    return the aggregate exit code. Precedence: any MALFORMED -> 2; else any FAIL -> 1; else 0.

    A --root that does not exist or is not a directory is a fail-closed MALFORMED (exit 2): a typo'd
    root must never read as an all-absent clean run."""
    root = Path(root)
    if not root.exists():
        print("conformance: --root path does not exist: {}".format(root), file=sys.stderr)
        return 2
    if not root.is_dir():
        print("conformance: --root path is not a directory: {}".format(root), file=sys.stderr)
        return 2
    root = root.resolve()
    # Fail-closed if the standards dir exists but cannot be listed. map_keys()/load_manifests() raise on
    # an unlistable dir (via _standards.ensure_listable) rather than letting Path.glob read it as empty,
    # so an unreadable .aiqt/standards/ becomes a structured exit 2 here, not a traceback or false-clean.
    try:
        _rebind_map_keys(root)
    except OSError as exc:
        print("conformance: cannot read {}: {}".format(_standards_dir(root), exc), file=sys.stderr)
        return 2
    cache = {}
    results = [
        check_c1(root, cache),
        check_c2(root, cache),
        check_c3(root),
        check_c4(root, cache),
        check_c5(root, cache),
    ]

    print("AIQT conformance: {}".format(root))
    for r in results:
        line = "  {} {:<13} {}".format(r.cid, r.label, r.status)
        if r.detail:
            line += " - {}".format(r.detail)
        print(line)

    # Behaviour is NEVER scored. It is surfaced honestly and points at the companion deck.
    print("  --  behaviour     NOT PROVEN - offline suite cannot establish behavioural conformance; "
          "see {}".format(BEHAVIOUR_DOC))

    # Coverage-qualified final line: enumerate per-check status; never an unqualified "conformant".
    passed = [r.cid for r in results if r.status == PASS]
    failed = [r.cid for r in results if r.status in (FAIL, MALFORMED)]
    na = [r.cid for r in results if r.status in (NA, SKIPPED)]
    parts = []
    if passed:
        parts.append("verified {}".format("/".join(passed)))
    if na:
        parts.append("NOT APPLICABLE {}".format("/".join(na)))
    if failed:
        parts.append("FINDINGS in {}".format("/".join(failed)))
    parts.append("behaviour NOT PROVEN")
    print("COVERAGE-QUALIFIED: " + "; ".join(parts))

    if any(r.status == MALFORMED for r in results):
        return 2
    if any(r.status == FAIL for r in results):
        return 1
    return 0


def _parse_args(argv):
    root = None
    self_test = False
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            root = Path(argv[i + 1])
            i += 2
        elif argv[i] == "--self-test":
            self_test = True
            i += 1
        else:
            print("usage: conformance.py [--root DIR] | --self-test", file=sys.stderr)
            return None
    return (root, self_test)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    root, self_test = parsed
    if self_test:
        return self_test_main()
    return run(root or Path.cwd())


# --- self-test --------------------------------------------------------------------------------------
# Proves the suite's honesty invariants against synthetic trees built with the tool's own functions:
#   1. a conformant tree passes (exit 0),
#   2. a drifted tree fails (non-zero),
#   3. a missing input degrades to NOT APPLICABLE (not a fake pass),
#   4. a real mapping finding (a cited id not in its manifest) fails (exit 1),
#   5. a typo'd --root fails closed (exit 2), not a hollow all-absent pass.

_APEX = """---
corpus-id: apex01
origin: pack
family: aiqt
apex: true
slug: project-integrity
---
# Project integrity

The apex rule for the self-test corpus.
"""

_ACCUR = """---
corpus-id: accur01
origin: pack
family: aiqt
tier: 10
facet: ACCUR
slug: read-before-characterizing
---
# Read before characterizing

A self-test accuracy rule.
"""

_SECI = """---
corpus-id: seci001
origin: pack
family: security
facet: SECI
slug: input-validation
---
# Validate external input

A self-test security rule.
"""

# A standards manifest and a rule that cites it, for the C4 mapping fail-case. The manifest declares
# exactly one real id (ST01); the rule below cites a fabricated one (ST99) to trip the finding.
_STD_MANIFEST = """map-key = "map-selftest"
name = "Self-test standard"
publisher = "AIQT self-test"
edition = "2026"
kind = "control"
status = "stable"
citation-unit = "control"
id-pattern = "ST[0-9]{2}"
source-artefact = "AIQT self-test fixture"
retrieved = "2026-01-01"
[[id]]
code = "ST01"
title = "The one real self-test control"
"""

_MAPPED_RULE = """---
corpus-id: seci002
origin: pack
family: security
facet: SECI
slug: output-encoding
map-selftest-tight: [ST99]
---
# Encode output

A self-test rule citing a fabricated standards id.
"""

# A BARE (fit-less) map key: after the rebind, map-selftest-tight/-broad are legal but map-selftest is
# not, so the generator rejects this rule as carrying an unknown key and the corpus fails to load (C1
# MALFORMED). Proves the enforcement lever behind the whole notation.
_BARE_KEY_RULE = """---
corpus-id: seci003
origin: pack
family: security
facet: SECI
slug: output-encoding
map-selftest: [ST01]
---
# Encode output

A self-test rule citing a real id under a BARE (fit-less) map key, no longer legal.
"""

# The same real id asserted BOTH tight and broad for one rule+framework: mutual exclusivity must reject
# it at C4 (a real finding, exit 1), proving that check is actually wired.
_BOTH_FITS_RULE = """---
corpus-id: seci004
origin: pack
family: security
facet: SECI
slug: output-encoding
map-selftest-tight: [ST01]
map-selftest-broad: [ST01]
---
# Encode output

A self-test rule asserting one id as both tight and broad, which mutual exclusivity forbids.
"""


def _build_conformant(base):
    """Write a full, self-consistent install under base using the tool's own generators, so the
    conformant tree is correct BY CONSTRUCTION. Returns the loaded corpus."""
    _rebind_map_keys(base)
    src = base / ".aiqt" / "core" / "rules"
    (src / "aiqt").mkdir(parents=True)
    (src / "security").mkdir(parents=True)
    (src / "aiqt" / "00-project-integrity.md").write_text(_APEX, encoding="utf-8")
    (src / "aiqt" / "read-before.md").write_text(_ACCUR, encoding="utf-8")
    (src / "security" / "input-validation.md").write_text(_SECI, encoding="utf-8")

    corpus = gen_rules.load_corpus(src)
    claude = base / ".claude" / "rules"
    for s, _fm, rel in corpus:
        target = claude / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(s.read_text(encoding="utf-8"), encoding="utf-8")

    pairs = [(s, fm) for s, fm, _ in corpus]
    pairs.sort(key=lambda pf: gen_agents.sort_key(pf[1]))
    (base / "AGENTS.md").write_text(gen_agents.render(pairs), encoding="utf-8")
    for adapter in gen_adapters.ADAPTERS:
        out = base.joinpath(*adapter["parts"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(gen_adapters.render(pairs, adapter["header"]), encoding="utf-8")
    cursor = base.joinpath(*gen_cursor.OUT_PARTS)
    for s_, _fm, rel in corpus:
        target = cursor / gen_cursor.cursor_rel(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(gen_cursor.render_rule(s_), encoding="utf-8")
    return corpus


def _build_mapped_tree(base, rule_text):
    """Write a minimal install: the apex rule, one mapped security rule (rule_text), and the selftest
    manifest declaring exactly ST01. Generates the .claude/rules + AGENTS.md surfaces so only the
    intended mapping check trips and the rest stays conformant. Shared by the C4 mapping fixture cases
    (fabricated id, both-fits). The rule_text must load (a rule whose key is illegal cannot be built
    this way, since load_corpus below would raise; see _build_bare_key)."""
    src = base / ".aiqt" / "core" / "rules"
    (src / "aiqt").mkdir(parents=True)
    (src / "security").mkdir(parents=True)
    std = base / ".aiqt" / "standards"
    std.mkdir(parents=True)
    (std / "selftest.toml").write_text(_STD_MANIFEST, encoding="utf-8")
    _rebind_map_keys(base)
    (src / "aiqt" / "00-project-integrity.md").write_text(_APEX, encoding="utf-8")
    (src / "security" / "output-encoding.md").write_text(rule_text, encoding="utf-8")

    corpus = gen_rules.load_corpus(src)
    claude = base / ".claude" / "rules"
    for s, _fm, rel in corpus:
        target = claude / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(s.read_text(encoding="utf-8"), encoding="utf-8")
    pairs = [(s, fm) for s, fm, _ in corpus]
    pairs.sort(key=lambda pf: gen_agents.sort_key(pf[1]))
    (base / "AGENTS.md").write_text(gen_agents.render(pairs), encoding="utf-8")


def _build_bare_key(base):
    """Write a minimal install whose one mapped rule uses a BARE (fit-less) map-selftest key, with the
    manifest present so map-selftest-tight/-broad ARE legal. After the rebind the generator rejects the
    bare key as unknown, so the corpus fails to load: C1 MALFORMED, exit 2. Cannot go through
    _build_mapped_tree (its load_corpus would raise on the illegal key), so no surfaces are generated;
    the run's own C1 load is what must fail closed."""
    src = base / ".aiqt" / "core" / "rules"
    (src / "aiqt").mkdir(parents=True)
    (src / "security").mkdir(parents=True)
    std = base / ".aiqt" / "standards"
    std.mkdir(parents=True)
    (std / "selftest.toml").write_text(_STD_MANIFEST, encoding="utf-8")
    _rebind_map_keys(base)
    (src / "aiqt" / "00-project-integrity.md").write_text(_APEX, encoding="utf-8")
    (src / "security" / "output-encoding.md").write_text(_BARE_KEY_RULE, encoding="utf-8")


_HOOKS_SCRIPT = """#!/usr/bin/env python3
# self-test hooks dispatcher stub
def stub_handler(data):
    return (0, None, None)
"""

_HOOKS_MANIFEST = """[plugin]
name = "aiqt-guardrails-hooks"
version = "0.1.0"
description = "Self-test hooks plugin."
author-name = "Self Test"
author-email = "selftest@example.invalid"
homepage = "https://example.invalid"

[[hook]]
id = "stub-control"
rules = ["seci001"]
platform = "claude-code"
event = "PreToolUse"
matcher = "Bash"
handler = "stub_handler"
default = "block"
class = "b"
residue = "A self-test control catches nothing real."
"""


def _build_hooks(base):
    """Add a hooks source tree (manifest + stub script) to an existing conformant install under base
    and generate its plugin surface via gen_hooks.build_desired, so C5 has both a source and a matching
    surface to compare. The manifest cites seci001, a real corpus-id in the conformant fixture."""
    hooks_src = base / ".aiqt" / "core" / "hooks"
    (hooks_src / "scripts").mkdir(parents=True)
    (hooks_src / "scripts" / gen_hooks.SCRIPT_NAME).write_text(_HOOKS_SCRIPT, encoding="utf-8")
    (hooks_src / "manifest.toml").write_text(_HOOKS_MANIFEST, encoding="utf-8")
    for rel, content in gen_hooks.build_desired(base, hooks_src).items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    def run_capture(root):
        # Capture stderr too: the bad-root case deliberately prints to stderr, and a self-test must not
        # leak expected diagnostics into an otherwise-clean gate log.
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            code = run(root)
        return code, buf.getvalue()

    def status_of(out, cid):
        """The status word reported for a check id, whitespace-insensitively (report padding varies)."""
        for line in out.splitlines():
            parts = line.split()
            if parts[:1] == [cid]:
                # line is: <cid> <label> <STATUS...> [- detail]; status is between label and any ' - '.
                head = line.split(" - ", 1)[0]
                return head.split(None, 2)[2] if len(head.split(None, 2)) > 2 else ""
        return None

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-conformance-selftest-"))
    except OSError as exc:
        # No writable temp dir (a locked-down sandbox): report cleanly and fail closed rather than
        # escaping as a traceback. Real CI/local runs always have one; this only guards odd environments.
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    # The 6 unreadable-input-dir cases (9-14) deny reads via chmod 0; a runner that can STILL read the
    # dir (root/DAC-bypass, or a filesystem that ignores POSIX bits) cannot exercise them. OBSERVE the
    # actual skip via os.access at each case rather than infer it from geteuid(), so the PASS summary's
    # coverage claim rests on what happened, not a proxy (claims-rest-on-observation).
    skipped_unreadable = []

    def _unreadable_or_skip(d, label):
        if os.access(d, os.R_OK):  # runner can read the chmod-0 dir: this case cannot run
            skipped_unreadable.append(label)
            return False
        return True
    # The fixtures rebind gen_rules.MAP_KEYS/SEQ_KEYS; restore them afterwards so a self-test invoked
    # in-process (an import, a notebook, a long-lived runner) never leaves the module globals mutated.
    saved_map_keys = gen_rules.MAP_KEYS
    saved_seq_keys = gen_rules.SEQ_KEYS
    try:
        # 1. Conformant tree -> exit 0, all four checks verified.
        good = tmp / "good"
        good.mkdir()
        _build_conformant(good)
        code, out = run_capture(good)
        if code != 0:
            failures.append("conformant tree expected exit 0, got {}\n{}".format(code, out))
        if status_of(out, "C3") != PASS or status_of(out, "C2") != PASS:
            failures.append("conformant tree expected C2/C3 PASS in report:\n{}".format(out))

        # 2. Drifted tree -> non-zero. Corrupt one generated .claude/rules file.
        drifted = tmp / "drifted"
        drifted.mkdir()
        _build_conformant(drifted)
        drift_target = drifted / ".claude" / "rules" / "security" / "SECI-input-validation.md"
        drift_target.write_text(drift_target.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
        code, out = run_capture(drifted)
        if code != 1:
            failures.append("drifted tree expected exit 1, got {}\n{}".format(code, out))
        if status_of(out, "C2") != FAIL:
            failures.append("drifted tree expected C2 no-drift FAIL:\n{}".format(out))

        # 2b. Surface drift -> C2 FAIL, exercised for EACH non-.claude generated surface (AGENTS.md and
        #     the two adapters GEMINI.md and the nested .github/copilot-instructions.md), so the pass
        #     line's per-surface claim is honestly tested and no surface is silently trusted. (.claude
        #     drift is case 2 above.)
        for rel in ("AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md",
                ".cursor/rules/aiqt-guardrails/aiqt/10-ACCUR-read-before-characterizing.mdc"):
            adrift = tmp / ("adrift-" + rel.replace("/", "_"))
            adrift.mkdir()
            _build_conformant(adrift)
            f = adrift / rel
            f.write_text(f.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
            code, out = run_capture(adrift)
            if code != 1 or status_of(out, "C2") != FAIL:
                failures.append("adapter-drift ({}) expected exit 1 + C2 FAIL:\n{}".format(rel, out))

        # 2c. An orphaned .mdc inside the reserved Cursor subtree (no source rule) -> C2 FAIL.
        corphan = tmp / "cursor-orphan"
        corphan.mkdir()
        _build_conformant(corphan)
        extra = corphan.joinpath(*gen_cursor.OUT_PARTS) / "aiqt" / "99-ORPHAN-extra.mdc"
        extra.write_text("---\nalwaysApply: true\n---\n\n# Orphan\n", encoding="utf-8")
        code, out = run_capture(corphan)
        if code != 1 or status_of(out, "C2") != FAIL:
            failures.append("cursor-orphan tree expected exit 1 + C2 FAIL:\n{}".format(out))

        # 3. Missing input (.aiqt/core absent) -> NOT APPLICABLE, not a fake pass. Keep only the
        #    placement-valid .claude/rules/ tree from a conformant build.
        thin = tmp / "thin"
        thin.mkdir()
        _build_conformant(thin)
        shutil.rmtree(thin / ".aiqt")
        (thin / "AGENTS.md").unlink()
        code, out = run_capture(thin)
        if code != 0:
            failures.append("thin tree expected exit 0, got {}\n{}".format(code, out))
        if status_of(out, "C1") != NA:
            failures.append("thin tree expected C1 NOT APPLICABLE:\n{}".format(out))
        if "NOT APPLICABLE" not in out or "COVERAGE-QUALIFIED" not in out:
            failures.append("thin tree must be coverage-qualified with NOT APPLICABLE, not a bare pass:\n{}".format(out))
        # It must NOT read as an unqualified conformant claim.
        if "conformant" in out.lower() and "COVERAGE-QUALIFIED" not in out:
            failures.append("thin tree over-claimed conformance:\n{}".format(out))

        # 4. A real mapping finding (a cited id not in its manifest) -> exit 1, C4 FAIL. Guards the
        #    --root-aware rebind: without it, load_corpus would reject the adopter's map-selftest key
        #    (not in the checker's own MAP_KEYS) and this would fail at C1 rather than surface at C4.
        badmap = tmp / "badmap"
        badmap.mkdir()
        _build_mapped_tree(badmap, _MAPPED_RULE)
        code, out = run_capture(badmap)
        if code != 1:
            failures.append("bad-mapping tree expected exit 1, got {}\n{}".format(code, out))
        if status_of(out, "C4") != FAIL:
            failures.append("bad-mapping tree expected C4 mappings FAIL:\n{}".format(out))
        if status_of(out, "C1") != PASS:
            failures.append("bad-mapping tree expected C1 PASS (rebind must admit the adopter map key):\n{}".format(out))

        # 4b. A BARE (fit-less) map key is no longer legal after the rebind: map-selftest-tight/-broad
        #     ARE legal (the manifest is present) but map-selftest is not, so the generator rejects the
        #     rule and the corpus fails to load: C1 MALFORMED, exit 2. This is the enforcement lever
        #     behind the whole fit notation, so it is exercised, not just asserted in prose.
        barekey = tmp / "barekey"
        barekey.mkdir()
        _build_bare_key(barekey)
        code, out = run_capture(barekey)
        if code != 2:
            failures.append("bare-key tree expected exit 2 (bare map key rejected), got {}\n{}".format(code, out))
        if status_of(out, "C1") != MALFORMED:
            failures.append("bare-key tree expected C1 MALFORMED (unknown map key):\n{}".format(out))

        # 4c. The same id asserted BOTH tight and broad for one rule+framework must FAIL C4 (exit 1):
        #     mutual exclusivity. Proves that check is actually wired, not merely present.
        bothfits = tmp / "bothfits"
        bothfits.mkdir()
        _build_mapped_tree(bothfits, _BOTH_FITS_RULE)
        code, out = run_capture(bothfits)
        if code != 1:
            failures.append("both-fits tree expected exit 1 (mutual exclusivity), got {}\n{}".format(code, out))
        if status_of(out, "C4") != FAIL:
            failures.append("both-fits tree expected C4 mappings FAIL (id both tight and broad):\n{}".format(out))

        # 6. Present-but-empty core with generated surfaces still on disk -> C2 must catch the orphans
        #    and FAIL (exit 1), not mask them as NOT APPLICABLE. (Regression guard for the empty-core hole.)
        emptycore = tmp / "emptycore"
        emptycore.mkdir()
        _build_conformant(emptycore)
        for md in (emptycore / ".aiqt" / "core" / "rules").rglob("*.md"):
            md.unlink()  # empty the core but keep the dir; leave .claude/rules + AGENTS.md in place
        code, out = run_capture(emptycore)
        if code != 1:
            failures.append("empty-core tree expected exit 1 (orphans), got {}\n{}".format(code, out))
        if status_of(out, "C2") != FAIL:
            failures.append("empty-core tree expected C2 no-drift FAIL (orphaned surfaces):\n{}".format(out))

        # 7. No .claude/rules tree at all -> C3 must be NOT APPLICABLE, never a hollow PASS.
        nosurface = tmp / "nosurface"
        nosurface.mkdir()
        _build_conformant(nosurface)
        shutil.rmtree(nosurface / ".claude")
        code, out = run_capture(nosurface)
        if status_of(out, "C3") != NA:
            failures.append("no-.claude/rules tree expected C3 NOT APPLICABLE, not a hollow PASS:\n{}".format(out))

        # 8. A malformed manifest with NO corpus -> exit 2, C4 MALFORMED. It must fail closed even though
        #    there is no corpus to cite it (the manifest is parsed before the absent-corpus short-circuit).
        badmanifest = tmp / "badmanifest"
        (badmanifest / ".aiqt" / "standards").mkdir(parents=True)
        (badmanifest / ".aiqt" / "standards" / "selftest.toml").write_text(
            "this is not valid TOML = = =\n", encoding="utf-8")
        code, out = run_capture(badmanifest)
        if code != 2:
            failures.append("malformed-manifest-no-corpus tree expected exit 2, got {}\n{}".format(code, out))
        if status_of(out, "C4") != MALFORMED:
            failures.append("malformed-manifest-no-corpus tree expected C4 MALFORMED:\n{}".format(out))

        # 9. An unreadable .aiqt/standards/ must fail closed (exit 2), not escape as a traceback. Skipped
        #    where the process can still read the dir despite the chmod (e.g. running as root), so the
        #    assertion stays deterministic across environments.
        unreadable = tmp / "unreadable"
        (unreadable / ".aiqt" / "standards").mkdir(parents=True)
        (unreadable / ".claude" / "rules").mkdir(parents=True)
        std_dir = unreadable / ".aiqt" / "standards"
        os.chmod(std_dir, 0)
        if _unreadable_or_skip(std_dir, "9 standards"):
            code, out = run_capture(unreadable)
            if code != 2:
                failures.append("unreadable standards dir expected exit 2 (fail-closed), got {}".format(code))
        os.chmod(std_dir, 0o755)  # restore so cleanup can remove it

        # 10. An unreadable .aiqt/core/rules/ dir must fail closed (exit 2), not read as an empty corpus
        #     (rglob silently yields nothing on an unlistable dir; load_corpus now probes it). Same
        #     root-vs-nonroot guard as case 9.
        badcore = tmp / "badcore"
        (badcore / ".aiqt" / "core" / "rules" / "aiqt").mkdir(parents=True)
        (badcore / ".claude" / "rules").mkdir(parents=True)
        core_dir = badcore / ".aiqt" / "core" / "rules"
        os.chmod(core_dir, 0)
        if _unreadable_or_skip(core_dir, "10 core-rules"):
            code, out = run_capture(badcore)
            if code != 2:
                failures.append("unreadable core-rules dir expected exit 2 (fail-closed), got {}".format(code))
            if status_of(out, "C1") != MALFORMED:
                failures.append("unreadable core-rules dir expected C1 MALFORMED:\n{}".format(out))
        os.chmod(core_dir, 0o755)  # restore so cleanup can remove it

        # 11. An unreadable GENERATED family dir (.claude/rules/aiqt) must fail closed via C2 (the orphan
        #     scan walks it), not conceal an orphan. Build a conformant tree, then make a generated family
        #     dir unreadable. Same root-vs-nonroot guard.
        badgen = tmp / "badgen"
        badgen.mkdir()
        _build_conformant(badgen)
        gen_fam = badgen / ".claude" / "rules" / "aiqt"
        os.chmod(gen_fam, 0)
        if _unreadable_or_skip(gen_fam, "11 generated-family"):
            code, out = run_capture(badgen)
            if code != 2:
                failures.append("unreadable generated family dir expected exit 2 (fail-closed), got {}".format(code))
            if status_of(out, "C2") != MALFORMED:
                failures.append("unreadable generated family dir expected C2 MALFORMED:\n{}".format(out))
        os.chmod(gen_fam, 0o755)  # restore so cleanup can remove it

        # 12. An unreadable .aiqt/ PARENT (not the standards/core dirs themselves) must fail closed:
        #     dir_present() stats THROUGH it and raises EACCES, so the run fails as exit 2 rather than
        #     reading the children as absent -> NOT APPLICABLE (the F-35 fail-open, where is_dir()
        #     swallows EACCES). Here map_keys()'s rebind trips first. Same root-vs-nonroot guard.
        badparent = tmp / "badparent"
        badparent.mkdir()
        _build_conformant(badparent)
        aiqt_dir = badparent / ".aiqt"
        os.chmod(aiqt_dir, 0)
        if _unreadable_or_skip(aiqt_dir, "12 .aiqt-parent"):
            code, out = run_capture(badparent)
            if code != 2:
                failures.append("unreadable .aiqt parent expected exit 2 (fail-closed), got {}\n{}".format(code, out))
        os.chmod(aiqt_dir, 0o755)  # restore so cleanup can remove it

        # 13. An unreadable .claude/ PARENT (the surface tree's parent, .aiqt readable) must fail closed
        #     via C2/C3 MALFORMED (exit 2), not read the .claude/rules surface as absent -> NOT APPLICABLE.
        #     Exercises the conformance _reachable() guards independently of the map_keys rebind.
        badclaude = tmp / "badclaude"
        badclaude.mkdir()
        _build_conformant(badclaude)
        claude_dir = badclaude / ".claude"
        os.chmod(claude_dir, 0)
        if _unreadable_or_skip(claude_dir, "13 .claude-parent"):
            code, out = run_capture(badclaude)
            if code != 2:
                failures.append("unreadable .claude parent expected exit 2 (fail-closed), got {}\n{}".format(code, out))
            if status_of(out, "C2") != MALFORMED and status_of(out, "C3") != MALFORMED:
                failures.append("unreadable .claude parent expected C2 or C3 MALFORMED:\n{}".format(out))
        os.chmod(claude_dir, 0o755)  # restore so cleanup can remove it

        # 14. An unreadable adapter parent dir (.github/, the copilot surface's nested parent) must fail
        #     closed as C2 MALFORMED (exit 2), not escape as a traceback: _adapter_drift's read probe
        #     is inside its fail-closed try. Same root-vs-nonroot guard as cases 9-13.
        badgithub = tmp / "badgithub"
        badgithub.mkdir()
        _build_conformant(badgithub)
        gh_dir = badgithub / ".github"
        os.chmod(gh_dir, 0)
        if _unreadable_or_skip(gh_dir, "14 adapter-parent"):
            code, out = run_capture(badgithub)
            if code != 2:
                failures.append("unreadable .github dir expected exit 2 (fail-closed), got {}".format(code))
            if status_of(out, "C2") != MALFORMED:
                failures.append("unreadable .github dir expected C2 MALFORMED:\n{}".format(out))
        os.chmod(gh_dir, 0o755)  # restore so cleanup can remove it

        # 15. An unreadable generated Cursor subtree must fail closed via C2 (the orphan scan walks it),
        #     not conceal an orphan. Same root-vs-nonroot guard as cases 9-14.
        badcursor = tmp / "badcursor"
        badcursor.mkdir()
        _build_conformant(badcursor)
        cur_dir = badcursor.joinpath(*gen_cursor.OUT_PARTS)
        os.chmod(cur_dir, 0)
        if _unreadable_or_skip(cur_dir, "15 cursor-subtree"):
            code, out = run_capture(badcursor)
            if code != 2:
                failures.append("unreadable cursor subtree expected exit 2 (fail-closed), got {}".format(code))
            if status_of(out, "C2") != MALFORMED:
                failures.append("unreadable cursor subtree expected C2 MALFORMED:\n{}".format(out))
        os.chmod(cur_dir, 0o755)  # restore so cleanup can remove it

        # 16. Conformant-with-hooks -> exit 0, C5 PASS. Also proves the no-hooks conformant fixtures
        #     (all cases above) keep C5 NOT APPLICABLE, never a hollow pass (case 1 asserts exit 0 with
        #     C5 absent from the FAIL set).
        withhooks = tmp / "withhooks"
        withhooks.mkdir()
        _build_conformant(withhooks)
        _build_hooks(withhooks)
        code, out = run_capture(withhooks)
        if code != 0 or status_of(out, "C5") != PASS:
            failures.append("conformant-with-hooks tree expected exit 0 + C5 PASS:\n{}".format(out))

        # 16b. Drifted hooks.json -> exit 1, C5 FAIL.
        hdrift = tmp / "hooks-drift"
        hdrift.mkdir()
        _build_conformant(hdrift)
        _build_hooks(hdrift)
        hj = hdrift.joinpath(*gen_hooks.PLUGIN_ROOT_PARTS) / "hooks" / "hooks.json"
        hj.write_text(hj.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        code, out = run_capture(hdrift)
        if code != 1 or status_of(out, "C5") != FAIL:
            failures.append("drifted hooks.json tree expected exit 1 + C5 FAIL:\n{}".format(out))

        # 16c. Orphan file under the reserved plugin hooks/ subtree -> exit 1, C5 FAIL.
        horphan = tmp / "hooks-orphan"
        horphan.mkdir()
        _build_conformant(horphan)
        _build_hooks(horphan)
        stray = horphan.joinpath(*gen_hooks.HOOKS_SUBTREE_PARTS) / "stray.txt"
        stray.write_text("stray\n", encoding="utf-8")
        code, out = run_capture(horphan)
        if code != 1 or status_of(out, "C5") != FAIL:
            failures.append("hooks orphan tree expected exit 1 + C5 FAIL:\n{}".format(out))

        # 16d. Hooks source present, surface deleted -> exit 0, C5 NOT APPLICABLE (the plugin is opt-in).
        hnosurface = tmp / "hooks-no-surface"
        hnosurface.mkdir()
        _build_conformant(hnosurface)
        _build_hooks(hnosurface)
        shutil.rmtree(hnosurface / "plugin")
        code, out = run_capture(hnosurface)
        if code != 0 or status_of(out, "C5") != NA:
            failures.append("hooks-source-only tree expected exit 0 + C5 NOT APPLICABLE:\n{}".format(out))

        # 16e. Surface present, hooks source deleted -> exit 1, C5 FAIL (orphaned enforcement surface).
        hnosource = tmp / "hooks-no-source"
        hnosource.mkdir()
        _build_conformant(hnosource)
        _build_hooks(hnosource)
        shutil.rmtree(hnosource / ".aiqt" / "core" / "hooks")
        code, out = run_capture(hnosource)
        if code != 1 or status_of(out, "C5") != FAIL:
            failures.append("hooks-surface-only tree expected exit 1 + C5 FAIL:\n{}".format(out))

        # 16f. An unreadable plugin hooks/ subtree must fail closed via C5 (exit 2), not conceal an
        #      orphan. Same root-vs-nonroot guard as cases 9-15.
        hbad = tmp / "hooks-unreadable"
        hbad.mkdir()
        _build_conformant(hbad)
        _build_hooks(hbad)
        hooks_surface = hbad.joinpath(*gen_hooks.HOOKS_SUBTREE_PARTS)
        os.chmod(hooks_surface, 0)
        if _unreadable_or_skip(hooks_surface, "16f hooks-surface"):
            code, out = run_capture(hbad)
            if code != 2:
                failures.append("unreadable plugin hooks/ expected exit 2 (fail-closed), got {}".format(code))
            if status_of(out, "C5") != MALFORMED:
                failures.append("unreadable plugin hooks/ expected C5 MALFORMED:\n{}".format(out))
        os.chmod(hooks_surface, 0o755)  # restore so cleanup can remove it

        # 5. A --root that does not exist fails closed (exit 2), never a hollow all-absent pass.
        code, out = run_capture(tmp / "does-not-exist")
        if code != 2:
            failures.append("nonexistent --root expected exit 2, got {}".format(code))
    finally:
        gen_rules.MAP_KEYS = saved_map_keys
        gen_rules.SEQ_KEYS = saved_seq_keys
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    skip_note = ("" if not skipped_unreadable else
                 " NOTE: {} unreadable-input-dir case(s) (9-15, 16f) were SKIPPED this run because the "
                 "runner could still read the chmod-0 dir (root/DAC-bypass): {}; run where POSIX read "
                 "bits are enforced to exercise them.".format(
                     len(skipped_unreadable), ", ".join(skipped_unreadable)))
    print("SELF-TEST PASS: conformant passes; drift (.claude/rules, AGENTS.md, the .cursor/rules Cursor tree, and the GEMINI.md/copilot "
          "adapters), empty-core orphans, fabricated mapping ids, a bare (fit-less) map key, and an id "
          "asserted both tight and broad fail; the hooks plugin surface (C5) verifies when present, "
          "degrades to NOT APPLICABLE when the opt-in plugin is absent, and fails on drift, a hooks "
          "orphan, or an orphaned surface with no source; absent input (corpus, "
          ".claude/rules) degrades to NOT APPLICABLE; malformed manifests, unreadable input dirs "
          "(standards, core rules, generated families, the .aiqt/.claude/.github parents, and the plugin "
          "hooks subtree), and a bad --root fail closed" + skip_note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
