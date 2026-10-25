# CLAUDE.md: AIQT Guardrails

**Version 0.2.11** (this file carries its own version, independent of the pack's SemVer release
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

AIQT Guardrails is a portable governance pack for AI coding assistants, published to aiqt.ai under the
Apache License 2.0 (except vendored third-party material, which remains under its own terms). This repo is its sole author going forward; `grc_library` is the frozen dogfood adopter with
a provenance pin. The pack ships a portable core plus generated platform adapters, versioned in SemVer
(first public release 1.0.5), with a per-file UTC Date for currency.

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
reads `actions/runs`, needing Actions: Read; under `--wait` it also reads pull requests: `pulls`, for
which a private repository's token also needs Pull requests: Read, and `commits/<sha>/pulls`, expected
to need the same (not verified live); see the script's header), never `gh pr checks` (a fine-grained token cannot read
the Checks API) and never `commits/<sha>/status` (which always reads pending). The gate roster grows
toward the full pack roster (portability, dogfood and adapter parity, version monotonicity).

Quality runs once per merge train, on the train head. `quality.yml` triggers on the `labeled`
pull-request type alone. After each push it makes to the train pull request, the merge lane removes the
`merge-train` label and adds it again, because adding a label the pull request already carries fires no
`labeled` event (GitHub's behaviour, not verified live here). A member push, open, reopen, retarget or
title/body edit schedules no Quality run, so a member head gets no Quality run at all: every Quality job
runs once, on the train head. Run `tools/run_all_checks.sh` before every member push: that local run is
the member head's evidence, and it fails when gitleaks is not on PATH or reports another version than the
gitleaks step of `quality.yml` pins (`tools/check_gitleaks_pin.py`). Nothing enforces that the local run
happens: it is a process rule, not enforced per head. Any label added to a pull request into main starts
a full run. A workflow-level concurrency group lets a newer run cancel the older one on the same
pull-request head branch; each push to main has a group of its own and is never cancelled, and
`tools/check_ci_parity.py` pins the block. `msg-leaks.yml` still runs on every pull request into main. A
head with no Quality run has no `Quality gates` status, which reads as missing, never as passing, and
`tools/ci-status.sh` reports it missing (exit 1) with or without `--require-base`; a second label event
while a head's run is in flight cancels that run, which `tools/ci-status.sh` reads as a failure of that
head, so only a refresh push, labelled again, recovers. The maintainer explicitly authorized (2026-10-25)
what member heads no longer get from CI, each still run once on the train head: every Quality job. The
gates in token parity with `tools/run_all_checks.sh` (gitleaks included) and the host-patch suites rest
on the local run alone; the CI-only pull-request legs (branch-root and version monotonicity against the
base), the hostile `core.autocrlf` byte-canon legs on ubuntu and macOS, setup-python's resolution of
`3.14` on the runner, evaluation of the merge ref against current main in a clean clone, and CI evidence
for each intermediate member commit have no member-head substitute.

CI runs two Python legs. Two required jobs in `.github/workflows/quality.yml`, `opf-host-patch` and
`git-fixture-env-host-patch`, run the OPF tooling self-test and the four git-fixture-env suites on
the exact patch release the maintainer host runs, named in `.aiqt/python-host.toml`, on
`runs-on: ubuntu-24.04`. Every other job there that sets up Python pins `3.14` (the Quality gates job sets
up none); with `check-latest` off, setup-python may resolve that pin to a 3.14 already on the runner rather
than the newest patch release. Every other job in `quality.yml` stays required. The host jobs add CI time,
a cost accepted for this signal: the OPF tooling self-test and the four git-fixture-env suites each run twice in
every run, and an edit to a pull request's title or body starts no Quality run. A passing local suite on the host
patch then predicts a required CI leg; a failure on the `3.14` legs alone is diagnostic evidence of a
patch-release difference, not proof that the change is innocent (a change once passed every local suite and
failed only in CI on a newer patch release). The runner image is pinned beside the patch: on 2026-10-12 the
setup-python versions manifest listed Linux x64 builds of 3.14.4 for Ubuntu 22.04 and 24.04 only, so
`ubuntu-latest` moving to an image without one would fail both host jobs, and moving them off ubuntu-24.04
(or its retirement) needs a host patch with a build for the new image. `tools/check_python_host_pin.py`
holds the workflow's top-level keys (only `name`, `on`, `concurrency`, `permissions` and `jobs`) and each
host job whole
(that runner, the checkout step as written, one setup-python pin, the gate itself as the next step, the
job's suite) and fails when the running interpreter is another release, so `tools/run_all_checks.sh` FAILS
on any patch release other than the one the file names. When the host interpreter changes, update
`python-host-patch` in `.aiqt/python-host.toml` and the `python-version` pin of both host jobs in
`quality.yml` together, in one change.

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
