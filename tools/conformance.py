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
import check_rule_placement as crp  # noqa: E402  check_name, check_drift, METADATA, BUILTIN_FAMILIES
from _standards import load_manifests, map_keys, natkey, ManifestError  # noqa: E402


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


def _corpus_src(root):
    return root / ".aiqt" / "core" / "rules"


def _standards_dir(root):
    return root / ".aiqt" / "standards"


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
    if not src.is_dir():
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
    if not claude_dir.is_dir():
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
            if fam_dir.is_dir():
                for f in sorted(fam_dir.rglob("*.md")):
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
    if not _corpus_src(root).is_dir():
        return Result("C2", "no-drift", NA, "no .aiqt/core/rules/ to regenerate from")
    corpus = cache.get("corpus", [])
    cs, cd = _claude_drift(root, corpus)
    as_, ad = _agents_drift(root, corpus)
    # Aggregate the two surfaces: worst status wins, but NA of one surface does not mask a PASS/FAIL of
    # the other. Precedence MALFORMED > FAIL > PASS > NA.
    order = {MALFORMED: 3, FAIL: 2, PASS: 1, NA: 0}
    worst = cs if order[cs] >= order[as_] else as_
    detail = "claude: {} ({}); agents: {} ({})".format(cs, cd, as_, ad)
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
    if not rules.is_dir():
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
    if not std.is_dir():
        return Result("C4", "mappings", NA, "no .aiqt/standards/ installed")
    # Parse the standards FIRST, fail-closed, BEFORE the absent-corpus short-circuit: a malformed or
    # unreadable manifest must surface as MALFORMED (exit 2), never hide behind an absent corpus and
    # read as clean. Catch OSError too (an unreadable manifest file is a read error, not a clean skip).
    try:
        manifests = load_manifests(std)
    except (ManifestError, OSError) as exc:
        return Result("C4", "mappings", MALFORMED, str(exc))
    if not src.is_dir():
        return Result("C4", "mappings", NA, "no .aiqt/core/rules/ installed to cite mappings")
    corpus = cache.get("corpus")
    if corpus is None:
        try:
            corpus = gen_rules.load_corpus(src)
        except (ValueError, OSError) as exc:
            return Result("C4", "mappings", MALFORMED, str(exc))

    findings = []
    mapped = 0
    for src_path, fm, _rel in corpus:
        for key in sorted(k for k in fm if k.startswith("map-")):
            ids = fm[key]
            if not isinstance(ids, list):
                findings.append("{}: {}: value must be a flow sequence".format(src_path.name, key))
                continue
            mapped += len(ids)
            manifest = manifests.get(key)
            if manifest is None:
                findings.append("{}: {}: no manifest under .aiqt/standards/ for this key".format(
                    src_path.name, key))
                continue
            if len(ids) != len(set(ids)):
                dupes = sorted({c for c in ids if ids.count(c) > 1})
                findings.append("{}: {}: duplicate id(s): {}".format(src_path.name, key, ", ".join(dupes)))
            if ids != sorted(ids, key=natkey):
                findings.append("{}: {}: ids must be natural-sorted; got [{}]".format(
                    src_path.name, key, ", ".join(ids)))
            for cid in ids:
                if not manifest.id_pattern.fullmatch(cid):
                    findings.append("{}: {}: id '{}' is malformed for {} ({})".format(
                        src_path.name, key, cid, manifest.name, manifest.edition))
                elif cid not in manifest.id_set:
                    findings.append("{}: {}: id '{}' is not in {} {}".format(
                        src_path.name, key, cid, manifest.name, manifest.edition))
    if findings:
        return Result("C4", "mappings", FAIL,
                      "{} issue(s): {}".format(len(findings), "; ".join(sorted(findings))))
    if mapped == 0:
        return Result("C4", "mappings", NA,
                      "no rule cites a mapping id to validate ({} manifest(s) installed)".format(
                          len(manifests)))
    return Result("C4", "mappings", PASS,
                  "{} mapping id(s) validate against {} manifest(s)".format(mapped, len(manifests)))


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
    # Fail-closed if the standards dir exists but cannot be listed. Path.glob (used by map_keys and
    # load_manifests) SILENTLY yields nothing on an unreadable directory rather than raising, which would
    # make an unreadable .aiqt/standards/ read as "empty standards" and pass clean. Force an os.scandir,
    # which does raise, so an unlistable standards dir becomes a structured MALFORMED exit 2.
    std_dir = _standards_dir(root)
    try:
        if std_dir.is_dir():
            list(os.scandir(std_dir))
        _rebind_map_keys(root)
    except OSError as exc:
        print("conformance: cannot read {}: {}".format(std_dir, exc), file=sys.stderr)
        return 2
    cache = {}
    results = [
        check_c1(root, cache),
        check_c2(root, cache),
        check_c3(root),
        check_c4(root, cache),
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
map-selftest: [ST99]
---
# Encode output

A self-test rule citing a fabricated standards id.
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
    return corpus


def _build_bad_mapping(base):
    """Write a minimal install whose one mapped rule cites a fabricated standards id, plus the
    manifest that id fails against. C4 must FAIL (exit 1); the rest stays conformant."""
    src = base / ".aiqt" / "core" / "rules"
    (src / "aiqt").mkdir(parents=True)
    (src / "security").mkdir(parents=True)
    std = base / ".aiqt" / "standards"
    std.mkdir(parents=True)
    (std / "selftest.toml").write_text(_STD_MANIFEST, encoding="utf-8")
    _rebind_map_keys(base)
    (src / "aiqt" / "00-project-integrity.md").write_text(_APEX, encoding="utf-8")
    (src / "security" / "output-encoding.md").write_text(_MAPPED_RULE, encoding="utf-8")

    corpus = gen_rules.load_corpus(src)
    claude = base / ".claude" / "rules"
    for s, _fm, rel in corpus:
        target = claude / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(s.read_text(encoding="utf-8"), encoding="utf-8")
    pairs = [(s, fm) for s, fm, _ in corpus]
    pairs.sort(key=lambda pf: gen_agents.sort_key(pf[1]))
    (base / "AGENTS.md").write_text(gen_agents.render(pairs), encoding="utf-8")


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

    tmp = Path(tempfile.mkdtemp(prefix="aiqt-conformance-selftest-"))
    failures = []
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
        _build_bad_mapping(badmap)
        code, out = run_capture(badmap)
        if code != 1:
            failures.append("bad-mapping tree expected exit 1, got {}\n{}".format(code, out))
        if status_of(out, "C4") != FAIL:
            failures.append("bad-mapping tree expected C4 mappings FAIL:\n{}".format(out))
        if status_of(out, "C1") != PASS:
            failures.append("bad-mapping tree expected C1 PASS (rebind must admit the adopter map key):\n{}".format(out))

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
        if not os.access(std_dir, os.R_OK):
            code, out = run_capture(unreadable)
            if code != 2:
                failures.append("unreadable standards dir expected exit 2 (fail-closed), got {}".format(code))
        os.chmod(std_dir, 0o755)  # restore so cleanup can remove it

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
    print("SELF-TEST PASS: conformant passes; drift, empty-core orphans, and fabricated mapping ids "
          "fail; absent input (corpus, .claude/rules) degrades to NOT APPLICABLE; malformed/unreadable "
          "standards and a bad --root fail closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
