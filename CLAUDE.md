# CLAUDE.md: AIQT Guardrails

**Version 0.2.5** (this file carries its own version, independent of the pack's SemVer release
version; bump it on every substantive change to this file).

This repository AUTHORS the portable AIQT Guardrails pack and the aiqt.ai site, and it dogfoods the
pack's own rules. It is not a corpus. This file is the operating governance for the orchestrator that
works here; the maintainer's private work method, account references, and host-local paths live in a
machine-local `CLAUDE.local.md` that is never committed here.

The portable rules this repo operates under are indexed in `.claude/RULES-INDEX.md` and carried in full under `.claude/rules/`
(generated from `.aiqt/core/rules/`), which loads into a Claude Code session automatically. The rest of
this file is the repo-specific operating governance that sits on top of those rules.

**Apex principle (highest precedence):** (Accuracy = Integrity = Quality = Trust) > Progress > Speed >
Cost. The four facets are one non-negotiable top tier; a gain in progress, speed, or cost never justifies
a loss on it. Its full rule is the first entry in the rule index (`.claude/RULES-INDEX.md`).

<!-- RULES-INDEX:BEGIN (generated) -->
## AIQT rule index

The rule corpus is indexed in [.claude/RULES-INDEX.md](.claude/RULES-INDEX.md), grouped by facet in AIQT priority order, and the full rule text loads from `.claude/rules/`. Both are generated from `.aiqt/core/rules/` (by `tools/gen_claude.py` and `tools/gen_rules.py`) and drift-gated in CI; do not hand-edit this block or the index file.
<!-- RULES-INDEX:END -->

## Project identity and product

AIQT Guardrails is a portable governance pack for AI coding assistants, published to aiqt.ai under CC
BY-SA 4.0. This repo is its sole author going forward; `grc_library` is the frozen dogfood adopter with
a provenance pin. The pack ships a portable core plus generated platform adapters, versioned in SemVer
(first public release 1.0.0), with a per-file UTC Date for currency.

## Operating decisions (repo-specific)

These are this repo's operating specifics. The portable rules they rest on are in the rule index (`.claude/RULES-INDEX.md`); only
what is particular to this repo (and has no source rule) lives here.

- **Confirmed defects are recorded.** Every confirmed defect gets a row in the operational
  `open-findings` register the moment it is confirmed, and leaves only via FIXED, ROUTED, REFUTED, or
  ACCEPTED. Severity is graded after the fix decision.
- **Verification floor.** This repo's standing floor is TRIPLE-family (a Claude-family, a GPT/Codex-
  family, and a Gemini-family verifier), for interoperability and platform neutrality; the only
  sanctioned reduction is a family's genuine unavailability (an outage or an exhausted rate limit, not
  merely a budget choice), noted and re-run when it returns. A further family or independent pass is
  reserved for critical changes. Quick, purely-bookkeeping changes need no standing verifier; the
  mechanical gates suffice.
- **Sole orchestrator.** ONE orchestrator is the sole writer and merge authority for this repo.
- **Change tracking.** The public `CHANGELOG.md` carries user-facing release notes per release,
  generated from `changelog.toml` and drift-gated; every change is recorded in detail in the operational
  record.

## Versioning and publication discipline

The pack uses SemVer, single-sourced in `changelog.toml` (the `version` on the latest `[[release]]`); the
root `VERSION` file is generated from it and drift-gated. The release-delta gate
(`tools/check_release_delta.py`) computes the minimum required bump over the governance surface. Releases
ship with a per-file manifest and a published ROOT digest, and those hashes are also published
independently on posluns.dev. By validating them you can identify whether the file you downloaded is the
same one we intended you to have.

## Gates and CI

CI runs a `Quality` workflow of deterministic gates: a project secret scan plus gitleaks, a
leak-denylist check, an en/em dash check, an internal-link check, a site-integrity check, roadmap,
changelog, rules, agents, and CLAUDE.md drift checks (generated files must match their sources), a
version format and single-source check, a rule-placement check, a standards-mappings check, an
adopter-conformance suite, an enforceability-ledger drift gate with a bidirectional roster-reconciliation check, and a standards-currency self-test. `tools/run_all_checks.sh` is the local mirror. Read CI status with `tools/ci-status.sh` (which
reads `actions/runs`, needing only Actions: Read), never `gh pr checks` (a fine-grained token cannot read
the Checks API) and never `commits/<sha>/status` (which always reads pending). The gate roster grows
toward the full pack roster (portability, dogfood and adapter parity, version monotonicity).

## Git and writing conventions

- Commits are authored `Jeff Posluns <jeff@posluns.ca>`, with NO AI author, committer, or co-author
  trailer.
- Oxford English; prefer `-ize`/`-ization`. **Never use en dashes or em dashes**; use hyphens, commas,
  colons, semicolons, or parentheses. Sentence-case headings. Console messages to the maintainer carry
  a `[YYYY-MM-DDTHH:MMZ]` UTC prefix and never render a wall of diff lines.

## Scope note

This file is a generated-adapter governance file: a hand-authored repo-specific layer wrapping one small
generated block that points to the generated rule index (`.claude/RULES-INDEX.md`, produced by `tools/gen_claude.py` from `.aiqt/core/rules/` and
drift-gated in CI, so it cannot diverge from the pack). The full rule text loads from `.claude/rules/`;
this file adds only what is particular to operating this repo. It will grow toward the full published-pack
governance (the complete gate/hook roster, the SemVer and publication discipline in full) as those land.
