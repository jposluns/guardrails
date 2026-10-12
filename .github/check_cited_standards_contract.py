#!/usr/bin/env python3
"""Pin this repository's cited-standards corpus and its --config wiring, outside the portable gate.

tools/check_cited_standards.py falls back to its built-in default corpus (the rule files alone) when a config
has no corpus line, and its self-test vectors carry their own corpus, so neither notices when this
repository stops scanning the OPF prompt pack. This assertion reads .aiqt/cited-standards.toml and fails
(exit 1) unless its corpus is present, names the rule-file glob, and expands, through the gate's own
expand_corpus, to every member opf/prompt-pack/pack.toml lists; and unless every cited-standards gate line
other than the self-test, in both tools/run_all_checks.sh and .github/workflows/quality.yml (read with
check_ci_parity's extractors), passes --config .aiqt/cited-standards.toml. A missing or malformed config,
manifest or runner, or a corpus glob the gate cannot expand, is cannot-evaluate (exit 2).

This guards declaration and wiring drift, not environment overrides, a member the gate scans and reads
short, or edits to this assertion.
"""
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from check_cited_standards import CannotEvaluate, expand_corpus, load_config  # noqa: E402
from check_ci_parity import extract_ci, extract_local, read_runner_text  # noqa: E402

CONFIG = ".aiqt/cited-standards.toml"
PACK = "opf/prompt-pack"
RULE_GLOB = ".aiqt/core/rules/*.md"
GATE = "tools/check_cited_standards.py"
RUNNERS = (("tools/run_all_checks.sh", extract_local), (".github/workflows/quality.yml", extract_ci))


def corpus_findings():
    """(findings, errors) for the config's corpus against the pack.toml member list."""
    try:
        globs, _exclusions = load_config(ROOT / CONFIG)
        data = tomllib.loads((ROOT / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError, CannotEvaluate) as exc:
        return [], ["cannot read {}: {}".format(CONFIG, exc)]
    if "corpus" not in data:
        return ["{} has no corpus line, so the gate falls back to its built-in default (the rule files "
                "alone) and no prompt-pack member is scanned".format(CONFIG)], []
    findings = []
    if RULE_GLOB not in globs:
        findings.append("{} corpus does not name the rule-file glob {!r}: {!r}".format(CONFIG, RULE_GLOB, list(globs)))
    try:
        manifest = tomllib.loads((ROOT / PACK / "pack.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        return findings, ["cannot read {}/pack.toml: {}".format(PACK, exc)]
    rows = manifest.get("members")
    if (not isinstance(rows, list) or not rows
            or not all(isinstance(row, dict) and isinstance(row.get("path"), str) and row["path"] for row in rows)):
        return findings, ["{}/pack.toml: members must be a non-empty array of tables, each with a "
                          "non-empty path".format(PACK)]
    members = ["{}/{}".format(PACK, row["path"]) for row in rows]
    try:
        scanned = expand_corpus(ROOT, globs)
    except CannotEvaluate as exc:
        return findings, ["{} corpus cannot be expanded: {}".format(CONFIG, exc)]
    missed = sorted(set(members) - set(scanned))
    if missed:
        findings.append("{} corpus misses {} member(s) {}/pack.toml lists: {}".format(
            CONFIG, len(missed), PACK, ", ".join(missed)))
    return findings, []


def wiring_findings():
    """(findings, errors) for the cited-standards gate lines of both runners."""
    findings, errors = [], []
    for source, extract in RUNNERS:
        text, diagnostic = read_runner_text(ROOT / source, source)
        if diagnostic is not None:
            errors.append("cannot read {}: {}".format(source, diagnostic.message))
            continue
        extraction = extract(text)
        if extraction.diagnostics:
            errors.append("cannot extract the gates of {}: {}".format(
                source, "; ".join(d.message for d in extraction.diagnostics)))
            continue
        lines = sorted(member for member in extraction.members
                       if member.split(" ")[0] == GATE and member != GATE + " --self-test")
        if not lines:
            findings.append("{} runs no cited-standards gate line besides its self-test".format(source))
        for member in lines:
            words = member.split(" ")
            if not any(words[i:i + 2] == ["--config", CONFIG] for i in range(len(words) - 1)):
                findings.append("{}: cited-standards gate line does not pass --config {}: {}".format(
                    source, CONFIG, member))
    return findings, errors


def main():
    findings, errors = corpus_findings()
    more_findings, more_errors = wiring_findings()
    findings, errors = findings + more_findings, errors + more_errors
    for line in errors:
        print("error: cited-standards repository contract: {}".format(line), file=sys.stderr)
    for line in findings:
        print("FAIL: cited-standards repository contract: {}".format(line), file=sys.stderr)
    if errors:
        return 2
    if findings:
        return 1
    print("PASS: cited-standards repository contract: {} names {!r} and covers every member {}/pack.toml lists, "
          "and both runners pass --config {}".format(CONFIG, RULE_GLOB, PACK, CONFIG))
    return 0


if __name__ == "__main__":
    sys.exit(main())
