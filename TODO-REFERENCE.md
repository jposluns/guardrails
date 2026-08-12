# TODO-REFERENCE

The detail companion to [`TODO.md`](TODO.md). `TODO.md` stays a clean, scannable index
(`Order | Work | ORIGIN`); this file carries the per-item depth: what each item **is**, what it
**needs/requires**, its **scope**, its **acceptance/done** criteria, and any dependency or
constraint notes. Entries are keyed by the same **Order id** as the TODO row, so it is a 1:1
lookup.

## Conventions

- One `## <Order> - <title>` section per live TODO row. When a row closes and leaves `TODO.md`,
  its reference section is removed here in the same PR (the pair stays in lock-step).
- **ORIGIN** traces the item to its originating grc_library planning id; it is provenance only,
  never guardrails identity (identity is the closing PR, per `TODO.md`).
- **Milestone** uses the ROADMAP pipeline: **M0** decouple, **M1** chat skill, **M2**
  dev-assistant pack, **M3+** later/candidate.
- This file is **public / product-facing** (Context-2/3 of the P-1.18 disclosure matrix). Build /
  orchestration detail (Context-1) lives in the private staging companion, never here.

---

# P1 - launch-critical

## 1.0.0 - Cost tier rule
**ORIGIN** 1.26.9 · **Milestone** M1 · **Size** M · CORE-portable

- **What it is:** the missing fourth AIQT tier as a portable governance rule. AIQT names Cost as
  its lowest priority, yet no rule governs it; this closes the most visible internal asymmetry.
- **Needs / requires:** composes with `project-integrity` (the tier ordering) and
  `session-lifecycle` (bounded retries). No external input.
- **Scope:** a rule bounding token and compute budgets, runaway agentic loops, unbounded fan-out,
  and the cost of the pack's own dual-family and high-assurance layers. Framed correctly: bound
  cost **without ever trading down the AIQT tier**; cap loops, recursion, and fan-out; escalate
  when the budget would force an AIQT compromise.
- **Acceptance:** a CORE rule file added; the Cost tier is governed, not just named.

## 1.1.0 - Human-oversight and autonomy-threshold rule
**ORIGIN** 1.26.11 · **Milestone** M1 · **Size** M · CORE-portable

- **What it is:** the headline topic of every AI-governance framework - how much autonomy for which
  risk class, and what forces a human into the loop - currently absent as a single rule.
- **Needs / requires:** mostly consolidation of existing pieces (`clarify-before-acting`,
  `express-authorization`, `decision-classification`, `session-lifecycle` modes, the reversibility
  gate), not net-new.
- **Scope:** one risk-tiered oversight rule with a scoped **EU AI Act Art. 14** touchpoint, cited
  conservatively and dated.
- **Acceptance:** a single risk-tiered oversight rule consolidates the scattered pieces; the Art. 14
  citation is verified and dated.

## 1.2.0 - NIST AI RMF and ISO/IEC 42001 framework symmetry
**ORIGIN** 1.26.10 · **Milestone** M1 · **Size** L · CORE-portable

- **What it is:** fixes a concrete asymmetry - the `ai/*` security rules map to OWASP LLM Top 10 /
  MITRE ATLAS / CSA AICM / NIST AI RMF, but the 15 governance rules (the actual product) map only
  to SSDF / CCM / ISO 27001 / ASVS, so they read as software governance rather than AI governance.
- **Needs / requires:** the source mapping largely exists in `project-integrity` and the corpus.
- **Scope:** add **NIST AI RMF** and **ISO/IEC 42001** columns (and a pack-level crosswalk) to the
  governance rules. Do NOT over-map ATLAS / LLM Top 10 onto the governance rules (attack taxonomies
  belong on the security rules).
- **Acceptance:** every added cell is a **PRESCRIBED** mapping verified against the framework text
  (the pack's own `claim-fit` / `lint-standards-currency` discipline) - never a plausible-looking
  id. A hallucinated cell in a pack that preaches citation precision is self-refuting.

## 1.3.0 - Portable rule-quality audit and consider-instead improvements
**ORIGIN** P-1.36 (rule slice) · **Milestone** M1 · CORE-portable

- **What it is:** the rule-quality half of "revisit every guardrail" - a prose-vs-mechanism audit
  of the governance rules, sharpening the rules the chat skill distills from.
- **Needs / requires:** seed = the migrated guardrail inventory. Unblocked at M1 (unlike the
  hook/gate slice, it depends on neither a findings loop nor unbuilt hooks).
- **Scope:** classify each rule MECHANICAL vs PROSE-ONLY; for each load-bearing prose rule, add a
  mechanical backstop or record it as model-sensitive; move toward the uniform
  `BLOCKED / WHY / CONSIDER INSTEAD` message form (CONSIDER, not RUN, so a mis-targeted suggestion
  is evaluated, not blindly executed).
- **Acceptance:** a bounded inventory-and-classify map plus a prioritized backlog of targeted
  backstops (not a monolith).

## 1.4.0 - Universal AIQT chat skill
**ORIGIN** 1.26.6 · **Milestone** M1 · **Size** L · CORE-portable

- **What it is:** one provider-neutral skill authored from the classified governance core - the
  deliverable chat platforms consume directly, and the source the coding-agent adapters are
  generated from.
- **Needs / requires:** the classified core; absorbs the portable parts of the former 4.1 / 3.130 /
  3.187 (do not build a competing skill family).
- **Scope:** a short AIQT card, a lean full protocol, explicit capability / no-tool degradation, the
  portable long-session-recovery discipline, and proportional verification. Three tiers (card, lean
  protocol, referenced deep modules) carry a ROUTING line so the situational deep modules
  (high-assurance, trust-recovery, conformance) are reached, not left as dead references.
- **Acceptance:** one dogfooded skill that a chat platform can consume as-is and adapters can be
  generated from.

## 1.5.0 - Conformance suite and normative contract
**ORIGIN** 1.26.7 · **Milestone** M1 · **Size** L · CORE-portable

- **What it is:** THE KEYSTONE (all three theory-craft lenses ranked it first). A library of
  adversarial scenario fixtures, one per discipline, each engineered to tempt a specific violation
  (dangle a false completion claim, execute off a discussion with no express go, obey an instruction
  injected in retrieved content, accept a set-completeness trap, silence a failing gate, ask for a
  findable fact), scored by **ACTION TRACE, not output vocabulary**.
- **Needs / requires:** build after the core is classified (1.26.5) and the universal skill authored
  (1.4.0). Note: authored WITHOUT the casebook seed corpus (2.8.0 defers to v1.1).
- **Scope:** the fixtures + a normative AIQT conformance contract. It simultaneously proves the pack
  works, IS the operational definition of model-independence (each assistant family passes a
  threshold of fixtures with the skill loaded), enables a conformance badge and a CI action, and
  gates community-contributed rules to quality. Self-validate the rubrics with the project's own
  mutation-testing-of-guards technique (who verifies the verifier).
- **Acceptance:** a passing conformance report per launch-claimed platform - the release gate for the
  freeze (1.8.0) and launch (1.9.0). Answers the pack's own top named risk: decorative compliance.

## 1.6.0 - Naming, licence, and contribution terms
**ORIGIN** 1.26.21 + 1.26.22 · **Milestone** M1 · **Size** S

- **What it is:** freeze the public name and licence posture before they enter release metadata and
  links.
- **Needs / requires:** do before launch (1.9.0).
- **Scope:** a collision scan against established products in this exact category (Guardrails AI,
  NeMo Guardrails, others), npm/PyPI availability of `aiqt`, a trademark screen, and one recorded
  naming decision; plus licence clarity - the whole project (prose AND code) is **CC BY-SA 4.0**
  (maintainer-final): a clear top-level LICENSE, per-surface notices, an attribution statement,
  accurate contribution terms (contributions accepted under CC BY-SA 4.0), and an optional DCO
  (`Signed-off-by`) for lightweight provenance.
- **Acceptance:** one recorded naming decision + a correct LICENSE and notices; the loose ShareAlike
  framing elsewhere is corrected to the actual terms.

## 1.7.0 - Disclosure and source-classification matrix, with sign-off
**ORIGIN** P-1.18 · **Milestone** M0 · **SIGNED OFF (maintainer, 2026-08-12)**

- **What it is:** the disclosure boundary that decides what may be published vs what stays private.
- **Status:** signed off; the M0 disclosure gate is CLEARED. Carry the signed decision forward.
- **Scope:** the three-context model - Context 1 BUILD INFRASTRUCTURE (never ships, in any form),
  Context 2 the development-assistant pack (capability ships, data instantiated empty at setup),
  Context 3 the chat skill. The published CORE contains no private corpus content requiring
  rewriting; the only residue is publication-time handling of pre-publication version-history rows.
- **Acceptance:** the signed matrix governs every later publish decision; a post-migration contamination scan confirms no Context-1 leakage.

## 1.8.0 - Zip provenance, final manifest, frozen release snapshot
**ORIGIN** 1.26.8 (subset) + 1.26.3 + 1.26.5-final · **Milestone** M1 · **Size** M

- **What it is:** the release layer a published product needs, and the freeze that becomes the first
  public release cut as SemVer **1.0.0**.
- **Needs / requires:** the final publication manifest (1.26.5-final) and a passing conformance
  report (1.5.0). Sequence BEFORE the installer (1.11.0), which depends on release governance.
- **Scope:** the launch SUBSET - each release records the source revision, the manifest digest, and
  the release version identifier, and the pre-publication source snapshot is frozen. The FULL
  release-governance layer (per-adapter digests, signed metadata, known-limitations, rollback,
  emergency-patch, deprecation windows) is 2.20.0, not here. SemVer with the **minor digit = the
  capability generation** (1.0.0 chat-assistant, 1.1.0 development-assistant); one product whose
  capabilities accrete, not a family of separate products.
- **Acceptance:** a reproducible, digested frozen snapshot cut as release 1.0.0; the full signing /
  rollback / deprecation governance that closes the publication-lag and currency-burden risks follows
  in 2.20.0.

## 1.9.0 - Initial repository-public and aiqt.ai launch
**ORIGIN** 1.26.20 · **Milestone** M1 · **Size** L

- **What it is:** the one-way EXTERNAL publication and the site launch (distinct from the M0 internal
  decouple).
- **Needs / requires:** GATED on a passing conformance report (1.5.0) for every launch-claimed
  platform, plus the naming (1.6.0) and licence (1.6.0) decisions; runs after classify / agnostic /
  freeze (1.8.0). Runs the Phase 8 visibility flip; Pages per the site runbook.
- **Scope:** export the frozen `guardrails/core/` and generated adapters to the public repo with the
  release provenance of 1.8.0, and launch aiqt.ai. **V1 launch surface:** quickstart, per-platform
  on-ramp, the conformance report, the framework crosswalk, the support matrix (2.7.0), the
  certification-ladder trust language (2.11.0), releases, and the contribution flow. The casebook
  (2.8.0) defers to v1.1.
- **Acceptance:** the repo is public and aiqt.ai is live with the v1 surface; launch success metrics
  (privacy-safe downloads, installer runs, badge adoptions) are defined.

## 1.10.0 - Site documentation and decision matrix
**ORIGIN** 1.26.35 · **Milestone** M1 · **Size** M

- **What it is:** the dual-audience canonical docs home on aiqt.ai.
- **Needs / requires:** the Level descriptions and the activity/cost model inputs.
- **Scope:** the 5 Level descriptions, the activity decision matrix with the cost-model column, the
  plain-language capability ladder, the chat-assistant story (CleanLanguage embedded as
  `references/` inside the AIQT chat skill; standalone skill an optional second install), and one
  aiqt.ai credit linking cleanlanguage.ai. Update-control positioning uses the sober outage
  case-study framing with correct figures, never vendor mockery.
- **Acceptance:** the docs + decision matrix are published and accurate to the shipped capability.

## 1.11.0 - Tiered adoption and the two-minute on-ramp
**ORIGIN** 1.26.13 · **Milestone** M1 · **Size** M · mixed (site project-only; adapters + card CORE)

- **What it is:** the literal landing-to-using-in-two-minutes adoption path the site needs.
- **Needs / requires:** release governance (1.8.0) for the installer; refines 1.4.0's tiering.
- **Scope:** a three-tier product (the AIQT card, the lean universal skill, the full pack plus
  adapters) shown as an explicit adoption ladder (Levels 0-4), with a per-platform copy button
  (clipboard gets the correctly-shaped file) and a one-command installer (`npx aiqt init` /
  `pipx run aiqt`) that detects the project's agent and drops the right adapter.
- **Acceptance:** an adopter can go from landing to a correctly-installed adapter in about two
  minutes, per platform.

---

# P2 - developer pack and planned progress

## 2.0.0 - Read-full-output extension to evidence-grounded completion
**ORIGIN** P-1.32 · early P2 (half-done), NOT launch-blocking · CORE-portable

- **What it is:** codify "read the full output, never tail-and-conclude" into the
  `evidence-grounded-completion` rule.
- **Scope:** when a worker delivery, subagent transcript, or command output is LARGE, read the full
  substantive content (map its structure; read every reasoning/answer/findings section) before
  characterizing it or concluding it is clean; never a `tail`/`head`/truncated slice as the basis
  for a conclusion. Extends the rule's pipe-masked-exit-code section to truncated reads.
- **Origin note:** a codex verifier's 833 KB transcript ended mid-dump and a tail read nearly
  produced a false "no findings"; the full read surfaced the real findings.
- **Acceptance:** the discipline lands in the rule (and its mirror), dual-family QA'd.

## 2.1.0 - Vendor cost-path verification
**ORIGIN** P-1.34 · dispatch early · **Size** S

- **What it is:** verify, per vendor, whether any subscription/OIDC path softens GitHub Actions
  metering (may make Level 2 plan-included for some accounts).
- **Scope:** recorded in a consuming PR; safe working assumption until verified is that GitHub is
  metered.
- **Acceptance:** each vendor's cost path is confirmed and recorded.

## 2.2.0 - Vendor data-handling verification
**ORIGIN** P-1.35 · dispatch early · **Size** S

- **What it is:** confirm each supported vendor's API data-handling posture (no-training tier
  availability, retention) for the CI path.
- **Scope:** so the L2/L3 docs' confidentiality statements ("the diff leaves GitHub") rest on
  verified vendor terms, not assumptions. Recorded in a consuming PR.
- **Acceptance:** each vendor's data-handling posture is verified and cited.

## 2.3.0 - Plugin and connector guardrail assessment
**ORIGIN** P-1.26 · assess with the Architect

- **What it is:** a colleague-raised question about security and guardrails around plugins and
  connectors.
- **Scope (to define at assessment):** whether it concerns (a) the pack's own rules re
  plugin/connector security (do the governance rules treat MCP connectors / plugins / third-party
  integrations as a trust boundary, akin to the publications-screening OWASP-LLM01/05 stance?),
  (b) corpus/guideline content for adopters, or (c) both.
- **Acceptance:** the assessment + named options surfaced to the maintainer.

## 2.4.0 - Deterministic coding-adapter generator and offline `--check`
**ORIGIN** adapter generator, ROADMAP M2 (dedicated id 1.26.52) · **Milestone** M2

- **What it is:** the generator that produces the coding-agent adapters deterministically from the
  core, plus a network-independent `--check`.
- **Needs / requires:** INPUT SET = the adapter-input bucket of the publication manifest (
  classifying pack files into core / adapter-input / grc-only). Sequence BEFORE the freeze.
- **Scope:** deterministic generation; the `--check` extends the byte-parity check to the generated
  adapters so drift, an unclassified core file, an unclassified adapter, or a hand-edited generated
  output all fail deterministically and offline. Output paths UNDECIDED - decide before building.
- **Acceptance:** generation is reproducible and `--check` fails closed on any drift, offline.

## 2.5.0 - Claude, Codex, ChatGPT, and editor adapter forms
**ORIGIN** adapter generator, ROADMAP M2 · **Milestone** M2

- **What it is:** the concrete adapter forms produced by 2.4.0.
- **Scope:** Claude Code (CLAUDE.md / `.claude/rules`), Codex/ChatGPT (AGENTS.md). Per-form
  acceptance: the named file appears at the platform's conventional path, byte-parity against the
  core is proved by the generator `--check`, and content differs from the core ONLY in placement,
  filename, frontmatter, or import syntax. Cursor/editor rules: UNBACKED - allocate an item or drop
  the commitment.
- **Acceptance:** each shipped adapter form is byte-parity-verified against the core.

## 2.6.0 - Coding-assistant install and download presentation
**ORIGIN** extends 1.26.20 + 1.26.35 · **Milestone** M2

- **What it is:** the per-agent install/download presentation on aiqt.ai.
- **Scope:** per-agent install/download; decide whether it is a new site section or an amendment to
  the shipped 1.10.0 pages (that answer sets whether it needs its own id).
- **Acceptance:** each supported agent has a clear install/download path on the site.

## 2.7.0 - Capability and compatibility matrix
**ORIGIN** 1.26.15 · **Milestone** M1-adjacent (bounds launch claims) · mixed

- **What it is:** a dated support matrix per tested assistant family - makes the universal claim
  honest and bounded rather than a `SKILL.md` label.
- **Needs / requires:** pass-rate input from the conformance suite (1.5.0); pairs with 1.8.0.
- **Scope:** per family: how the skill is supplied, whether it persists across turns, conformance
  pass rate, plus the Level, trigger-class, and cost-model dimensions. Includes a MODEL-CHURN
  re-validation cadence (re-run conformance on each new major family/version; the row carries the
  tested version and date).
- **Acceptance:** an accurate, dated per-platform expectation that bounds the site's claims.

## 2.8.0 - Provenance-as-proof and the sanitized caught-defect casebook
**ORIGIN** 1.26.12 · **disclosure-gated** · deferred to v1.1 · CORE-portable after clearance

- **What it is:** make the moat visible - the disciplines are earned from real incidents, which no
  style-prompt pack can claim.
- **Scope:** surface, at a safe abstraction, one real caught-defect per discipline (the failure
  shape, what the rule did, the class of defect prevented) with all project specifics stripped, plus
  the provenance table (rule from the failure class that created it). Doubles as the seed corpus for
  the conformance fixtures.
- **Disclosure:** HARD-gated on the P-1.18 matrix - generalize to failure CLASS; keep the safe
  aggregate; never ship PR numbers or private topology. (The raw evidence is Context-1; only the
  sanitized derivative is public - see the private companion.)
- **Acceptance:** a sanitized casebook cleared against the disclosure matrix; deferred to v1.1.

## 2.9.0 - Dogfooding outcome scorecard
**ORIGIN** 1.26.19 · **disclosure-gated** · CORE-portable after clearance

- **What it is:** privacy-safe aggregate evidence that the pack works.
- **Scope:** disciplines traced to failure classes, conformance pass-rate trends, and the
  **dual-family divergence rate** (how often the second model family caught what the first missed),
  which empirically justifies the pack's most distinctive control. Public adopter COUNTS are never
  presented.
- **Disclosure / integrity:** governed by the pack's own measured-vs-estimated discipline (never sum
  a measured figure with an estimated one; UNKNOWN never zero) or it is self-refuting. (Raw metrics
  are Context-1 - see the private companion.)
- **Acceptance:** a privacy-safe scorecard cleared against the disclosure matrix.

## 2.10.0 - AIQT threat model and responsible disclosure
**ORIGIN** 1.26.16 · CORE-portable

- **What it is:** the security posture a distributed governance artefact needs.
- **Scope:** a threat model covering malicious contributions, adapter tampering, prompt injection
  through referenced content, and a responsible-disclosure process; plus the distribution supply
  chain this project creates (adapter / installer / package integrity) and the new surfaces (CI-kit
  secrets path, /contributions ingestion, updater fetch path, Level-4 broker). Sequenced WITH the
  mechanisms it models, not before them.
- **Acceptance:** a published threat model + responsible-disclosure process covering the shipped
  surfaces.

## 2.11.0 - Cautious certification ladder
**ORIGIN** 1.26.18 · **Size** S · bounds launch trust language

- **What it is:** positioning that avoids certification theatre and liability.
- **Needs / requires:** the conformance report (1.5.0) and the support matrix (2.7.0).
- **Scope:** begin with a self-check, then a reproducible conformance report, then - only with
  independent programme governance - reviewed attestation language. Never claim
  certified/compliant/universal beyond what the recorded evidence bounds. Governs the trust language
  across the site and README.
- **Acceptance:** trust language everywhere is bounded by recorded evidence.

## 2.12.0 - AI incident-response discipline
**ORIGIN** 1.26.17 · CORE-portable

- **What it is:** completes the lifecycle (prevent, verify, recover-process, and now
  RESPOND-to-escape) - a rule governing a harmful AI output or action that shipped: contain, roll
  back, disclose, learn.
- **Scope:** built from existing raw material (the revert-path override register, the
  artefact-and-branch rollback discipline). `trust-recovery-escalation` recovers trust after a
  discipline lapse; this governs a shipped harmful result. Maps to NIST AI RMF Manage and
  incident-reporting regimes.
- **Acceptance:** a CORE incident-response rule that closes the post-escape gap.

## 2.13.0 - CleanLanguage as a separately sourced AIQT module
**ORIGIN** 1.26.46 · **Size** M

- **What it is:** vendor the CleanLanguage skill as a standalone-origin AIQT module.
- **Scope:** byte-identical PROVENANCE/LICENSE/NOTICE vendoring; always-latest fetch of
  cleanlanguage.zip from cleanlanguage.ai; module activation-mode config under
  `.working/aiqt/config.md` (always-on / large-prose / doc-generation / MCP surfaces);
  AIQT-supersedes-standalone precedence when both are installed; all-assistant chat packaging
  mirroring the cleanlanguage.ai distribution.
- **Acceptance:** CleanLanguage installs and updates as an AIQT module without divergence from
  upstream.

## 2.14.0 - Guardrails-native hook and gate inventory
**ORIGIN** P-1.36 (hook/gate slice) · **Milestone** M3+ (local machinery)

- **What it is:** the hook/gate half of "revisit every guardrail" - an inventory of guardrails'
  OWN native hook/gate set.
- **Needs / requires:** seeded BY the migrated guardrail inventory (and its section-E
  prose-to-mechanized candidate set). Sequenced AFTER the guardrail-mechanization PRs.
- **Scope:** the output is guardrails' own native set - NOT all 17 hooks and all 84 grc gates; the
  inventory already narrowed it to section E's four viable candidates. Everything else in the prose
  set is inherently judgment: leave prose + sharpen.
- **Acceptance:** guardrails' native hook/gate inventory exists, seeded from the migrated map.

## 2.15.0 - Waiting-word Stop hook, native rebuild
**ORIGIN** seed of 1.26.48 (vocabulary), superseded design

- **What it is:** a Stop-hook that keeps a session from yielding on a stated-but-unkept intention -
  rebuilt guardrails-native.
- **IMPORTANT:** the earlier vocabulary/regex approach was **RETIRED 2026-08-10 as fundamentally
  flawed** (unbounded natural-language trigger space; never fired). The rebuild is keyed on
  **OBSERVABLE runnable-work state**, not the vocabulary approach; the guardrails project owns
  designing it.
- **Scope:** gate turn-end on whatever outstanding work the harness can actually observe (a delivered
  result not yet consumed, a branch carrying unmerged work); fail open; honour an explicit escape;
  read the continuation signal so it has a terminating condition.
- **Acceptance:** an observable-state Stop discipline (not a vocabulary matcher).

## 2.16.0 - Guardrails operational hooks batch (with regressions)
**ORIGIN** 1.26.50 · **Size** M · hook type

- **What it is:** the PR2 batch of operational hooks, built self-verifying + dual-family.
- **Scope:** the self-applied `[BLOCKED]`-tag refusal, a truncated-read warning, a private-store
  pre-push validate, an auto-resume 3-condition gate, and a commit-before-dispatch pinned-SHA guard;
  the `block-large-editwrite-payload` hook is revivable here.
- **Acceptance:** the batch lands with regression fixtures, each hook self-verifying.

## 2.17.0 - Guardrails attestation gates batch (with regressions)
**ORIGIN** 1.26.51 · **Size** L · gate type

- **What it is:** the PR3 batch of attestation gates.
- **Scope:** a worker-id/account anonymization lint, a dual-family attestation gate, the
  ledger-attestation gate family (over the project's QA, model-performance, cost, and session ledgers),
  and a contract-consistency / config-parity gate (the machine that would catch cross-doc
  contradictions).
- **Acceptance:** the batch lands with regression fixtures.

## 2.18.0 - Activity and configuration parity gate
**ORIGIN** 1.26.42 · **Size** S

- **What it is:** a gate ensuring every configurable activity is actually configured and documented.
- **Scope:** an activity manifest tagging each skill/command Category A/B/C; the gate checks every
  Category-C activity has its `config.md` entry (variable, recommended default, options) and its
  decision-matrix row - presence-and-shape only, never judging the default value; scoped to
  Category-C additions so it cannot cry wolf.
- **Acceptance:** a fail-closed parity gate scoped to Category-C, non-noisy.

## 2.19.0 - Findings loop v1
**ORIGIN** 1.26.32 · **Size** M

- **What it is:** the CI-findings handling loop.
- **Scope:** CI findings are VALIDATED before action; auto-fix DEFAULT ON, user-configurable, capped
  at 3 tries on the same PR then a LOUD maintainer alert; consumed at the assistant's next session
  start PLUS a scheduled re-surface after N days so nothing waits indefinitely; at merge/promotion
  points the question returns to the developer.
- **Acceptance:** a validated, capped, resurfacing findings loop.

## 2.20.0 - Full reproducible release provenance and rollback governance
**ORIGIN** 1.26.8 (full) · **Size** M

- **What it is:** the complete release-governance layer (1.8.0 ships the launch subset; this is the
  full scope).
- **Scope:** signed release metadata, per-adapter digests, known-limitations, rollback, an
  emergency-patch process, deprecation windows, and the release-snapshot manifest plane (a release
  pins the per-guardrail component-version list). Composes with the generator `--check` and the
  publication.
- **Acceptance:** releases are fully reproducible with a governed rollback and patch path.

## 2.21.0 - Remaining machinery consolidation phases
**ORIGIN** 1.26.1 (remainder) · **Size** XL

- **What it is:** the remaining in-project machinery consolidation/simplification not already done.
- **Scope:** consolidate and simplify the pack-coupled machinery so the distributed core is clean.
- **Acceptance:** the remaining consolidation phases land.

## 2.22.0 - ShareAlike contribution integration
**ORIGIN** 1.26.2 · **Size** L · POST (not a freeze input)

- **What it is:** integrate the community's ShareAlike contributions once the repo is public and
  contributions can exist.
- **Scope:** triage each contribution, validate it against the project's standards, and fold the
  accepted ones into the machinery and the pack, credited per the licence. Real-world local-model
  use is the sharpest signal for what the tool-agnostic distribution must get right.
- **Acceptance:** accepted contributions are integrated and credited. (Not a freeze prerequisite:
  the pack is private until the visibility flip, so no contribution can exist to integrate before
  then.)

## 2.23.0 - AIQT logs and metrics under `.working/aiqt/`
**ORIGIN** 1.26.34 · **Size** M

- **What it is:** the operational logs and usage history that feed memory-refresh and the future
  portal.
- **Scope:** per-run records, coverage percentage, escaped-defect trend, spend vs plan, top open
  risks (the executive four numbers derive from these), in a portal-forward shape.
- **Acceptance:** the metrics substrate exists under `.working/aiqt/`.

## 2.24.0 - Three-layer adopter conventions
**ORIGIN** 1.26.36 · **Size** S · M1 input to the freeze

- **What it is:** the phase-1 form of the three-layer separation, written BEFORE the core is frozen
  so the first release is born modular.
- **Scope:** AIQT core (versioned, componentized) / adopter config (preserved) / assistant-added
  local guardrails (preserved, LOCAL-numbered) - as conventions + directory shape. The full updater
  is 2.25.0.
- **Acceptance:** the directory shape + conventions land ahead of skill authoring and are a HARD
  input on the freeze checklist (retrofitting later would be a breaking change to every install).

## 2.25.0 - Guardrail identity and update mechanism
**ORIGIN** 1.26.39 · **Size** L

- **What it is:** the modular auto(ish)-updater with a versions-manifest.
- **Scope:** per-guardrail `AIQT-######` identity (sequential, permanence-checked, allocated by repo
  tooling at adoption) with per-guardrail CalVer; a machine-readable versions manifest (id, CalVer,
  sha256, advisory flag, min-core) in the repo, mirrored at aiqt.ai beside the zips; the zip is the
  transport unit, the guardrail the APPLY unit; per-guardrail policy auto|offer|pin|skip; security
  advisories auto-apply by default and LOUD-alert on overridden guardrails, never force; layer-2/3
  never clobbered; multi-origin (AIQT core + CleanLanguage). Also mechanizes the grc pin-bump path.
- **Acceptance:** a working modular updater with stable identities and a signed manifest.

## 2.26.0 - Scheduled trigger class and adopter gates
**ORIGIN** 1.26.41 · **Size** M

- **What it is:** add SCHEDULED (cron) as the fourth trigger class, plus a pluggable adopter-gates
  component.
- **Scope:** SCHEDULED everywhere it makes sense; and the adopter's own deterministic gates/linters
  run as a pluggable component locally + in CI (grc_library's gate suite is just this project's
  instance of that component).
- **Acceptance:** a scheduled trigger class + a pluggable gates component.

## 2.27.0 - Local second-opinion CLI
**ORIGIN** 1.26.33 · **Size** M · Level-1

- **What it is:** the minimal Level-1 local cross-family review CLI.
- **Scope:** a `/cross-qa` sibling-CLI flow - local cross-family review driven by the coding
  assistant, on-request or automatic at named steps, default automatic at high-value steps only,
  pre-push above all.
- **Acceptance:** a working local second-opinion flow (Phase 2 completes the full L1 surface).

## 2.28.0 - Static role-tiered report
**ORIGIN** 1.26.43 · **Size** M

- **What it is:** the interim portal - a generated static roll-up from the `.working/aiqt/` logs.
- **Scope:** a Markdown / static-HTML roll-up with tiers as report sections (developer detail
  flowing up to the executive four-number view). Validates metrics + tier design + demand before any
  SaaS.
- **Acceptance:** a generated role-tiered report from real metrics.

## 2.29.0 - Plan-corpus drift sweep and ownership
**ORIGIN** 1.26.24 · **Size** M

- **What it is:** a housekeeping sweep of the plan corpus itself.
- **Scope:** add supersession markers and redirect stubs for absorbed items, refresh the migration
  inventory, and assign owners to the two unowned decisions.
- **Acceptance:** the plan corpus is drift-clean with owners assigned.

## 2.30.0 - Operating-modes discipline (documented CORE feature)
**ORIGIN** 1.26.25 · **Size** M · CORE-portable

- **What it is:** formalize the operating-modes model into a documented CORE discipline.
- **Scope:** the daytime matrix {attended | unattended} x {autonomous | ask-always}; overnight
  conservative-reversible-else-record; the RETURN transition (swap to attended, ask autonomous vs
  ask-always); the 10-minute no-answer swap to unattended as a sanctioned CONSERVATIVE degradation,
  reconciled with the invariant that EXITING unattended still requires an operator act. Builds on
  the `session-lifecycle` pack rule.
- **Acceptance:** the full mode matrix, triggers, and transitions are explicit and portable.

## 2.31.0 - Session-continuity discipline (documented CORE feature)
**ORIGIN** 1.26.26 · **Size** M · CORE-portable

- **What it is:** formalize wind-down / resume / crash-recovery into a documented CORE discipline,
  for repo-backed AND local work.
- **Scope:** the durable handoff/resume record (single resume point, reconciled not appended);
  active-session detection + the concurrency interlock (lease + external cross-check; a live-looking
  session HOLDs for confirmation); the closing green wind-down; and recovery of a crashed or
  not-wound-down session. Pairs with 2.30.0.
- **Acceptance:** the full methodology is explicit, portable, and documented.

## 2.32.0 - Opt-in update-check mechanism (CORE feature)
**ORIGIN** 1.26.27 · CORE-portable

- **What it is:** an opt-in mechanism so an adopter's AI notices a newer AIQT version and alerts the
  user, without the skill itself making any external call.
- **IMPORTANT (one-way door):** the skill-carried half is PRE and must ship inside 1.4.0 / 1.9.0 -
  the payload carries its own `SELF_VERSION`, the manifest URL pair, and the protocol text; a v1
  shipped without them can never alert its adopters to a later release. The host-side fetch / compare
  / alert / cadence / signing is the POST half.
- **Scope:** stateless instructions; the HOST AI performs fetch/compare/alert with its own tools only
  when opted in; a static version manifest at aiqt.ai/version.json and the raw repo path; signed;
  session-start cadence with a quiet one-line current status and an ALERT only when an update exists;
  no phone-home by default.
- **Acceptance:** the PRE fields ship in v1; the host-side half follows.

## 2.33.0 - Session-health and degradation-awareness discipline (CORE feature)
**ORIGIN** 1.26.28 · **Size** M · CORE-portable

- **What it is:** track compaction events and session duration and advise the user of degradation /
  hallucination risk on longer sessions and after more compactions - MODEL-DEPENDENT.
- **Scope:** surface the risk only on a NAMED, externally-observable signal (a compaction tally
  crossing a threshold, a duration, a quotable self-inconsistency), never an un-instrumented "I feel
  degraded"; let the user decide (continue / start fresh / hand off / auto-resume). References the
  one-shot auto-resume mechanism for delicate-model overnight cases. Pairs with 2.30.0 / 2.31.0.
- **Acceptance:** the tracking, model-dependent advisory, and user-choice surfacing are explicit and
  portable.

## 2.34.0 - Portable `/sitrep` skill
**ORIGIN** 3.142 · CORE-portable · off the release critical path

- **What it is:** a provider-neutral `/sitrep` skill that dogfoods `evidence-grounded-completion`.
- **Scope:** a situation report composed LIVE from instruments at invocation (never from in-context
  memory), in six sections (work in flight, the queue, QA state, the worker/agent fleet, decisions
  owed to the human, an honest usage/cost footer). Every figure traceable to its instrument;
  measured and estimated figures in separate columns, never summed; an unyielded figure reads
  UNKNOWN, never zero.
- **Acceptance:** a CORE `/sitrep` skill (the adopter wires its own instruments in `## Project
  wiring`).

## 2.35.0 - Citable CC BY-SA methodology and reference model
**ORIGIN** 4.31 · **Size** M

- **What it is:** publish the guardrails pack as a citable CC BY-SA methodology / reference model.
- **Scope:** the failure-mode provenance, the enforcement mechanism, the results - as adopter-facing
  methodology, independent of any project-specific machinery.
- **Acceptance:** a citable methodology publication, cross-referenced to the reconciled pack.

## 2.36.0 - Port the reference-currency playbook
**ORIGIN** grc playbook · LOW · **Milestone** M3+

- **What it is:** adapt the reference-version-currency playbook to pack citations.
- **Scope:** the held-vs-upstream check order and the missing-reference acquisition SOP, adapted so
  the pack's own external-standard citations stay current.
- **Acceptance:** a portable reference-currency discipline for pack citations.

---

# P3 - new guardrails intake

No live migrated P3 row (after the waiting-word item finishes/retires at M0). Two lanes: community-submitted
guardrails (PR per spec, once contribution conventions land and the repo is public) and grc_library
guardrail packages via the shared drop (rule + incident provenance). Create one reference section
per accepted package when its row is created; a genuine guard-defect package moves to P1 and, on
release, triggers the grc pin-bump (the sole freeze exception). Never preallocate placeholders.

---

# P4 - future and community

## 4.0.0 - Community failure-mode flywheel
**ORIGIN** 1.26.14 · CORE-portable process

- **What it is:** turn adopters into contributors - a submit-a-failure-mode-get-a-rule intake that
  triages adopters' own agent-failure incidents into new guards (credited per ShareAlike).
- **Scope:** the flywheel scope minus contribution governance (which lives in 4.4.0); regenerates the
  provenance stories that are the moat. The community half is explicitly DEFERRED until the
  capability is mature with organic demand.
- **Acceptance:** a working failure-mode intake, activated when demand exists.

## 4.1.0 - Early pilot cohort
**ORIGIN** 1.26.23 · **Size** M

- **What it is:** a small invited pilot cohort in place of a public teaser.
- **Scope:** a handful of the team plus trusted adopters already running the pack on local models,
  against the near-final core and the conformance suite, under an honest pre-release framing; closes
  the no-external-signal gap at lower exposure than a public teaser. Folds the reference pilot
  (4.2.0 is its concrete first instance).
- **Acceptance:** pilot feedback validates the discipline and on-ramp before public launch.

## 4.2.0 - Reference Level-3 pilot
**ORIGIN** 1.26.37 · **Size** M

- **What it is:** the org-agnostic internal-repos reference pilot (Level-3 team shape).
- **Scope:** org secret, selective triggering, team cost visibility, weekly static roll-up; success
  = the assurance story AND developer adoption, measured on existing coverage + escaped-defect
  metrics, never public adopter counts. Any specific-org arrangement stays private and unlogged.
- **Acceptance:** a run reference pilot with recorded (private) outcomes.

## 4.3.0 - Promotion-model panel
**ORIGIN** 1.26.38 · **Size** M

- **What it is:** the full-model deep-QA panel at promotion events.
- **Scope:** deep QA at major/minor version bumps or management-defined promotion events; the full
  panel including the developer's own model (a fresh-context adversarial pass), apples-to-apples;
  bounded by trigger rarity; feeds the model-performance baseline. Advisory.
- **Acceptance:** a defined promotion-trigger panel that feeds the QA baseline.

## 4.4.0 - Contribution-back lifecycle and community intake
**ORIGIN** 1.26.40 · **Size** M

- **What it is:** the conventions + governance that sustain the flywheel.
- **Scope:** additive seed-file PRs at `/contributions/` (never core-editing); accepted / rejected /
  withdrawn dispositions with rationale headers; a CONTRIBUTORS thank-you register;
  sanitize-before-publicity + a scanning gate on `/contributions/`; adopted seeds get a NEW AIQT
  number with the origin chain in the guardrail header. Community launch stays DEFERRED on maturity
  + organic demand.
- **Acceptance:** a working, governed contribute-back lifecycle.

## 4.5.0 - Community comms
**ORIGIN** new

- **What it is:** the community communication channels.
- **Scope:** a forum, a group channel, and announcement channels (per the maintainer's recorded
  scope).
- **Acceptance:** the channels exist and are announced.

## 4.6.0 - Phase-3 tail: additional families and hosted concepts
**ORIGIN** 1.26.45 · **Size** XL · sequenced LAST

- **What it is:** the remaining Phase-3+ set, worked as sub-items when reached.
- **Scope:** a Level-4 Live spike (MCP broker/connector, keys never exposed); a portal SaaS
  (findings + metadata only, no code copies; Zero Trust; freemium with an org-based boundary);
  contribute-back pipeline automation (receive / curate / verify / generalize / credit, with
  dual-family verification of contributed seeds); additional model families + GitLab; a hosted
  broker. Closes with the revisit of whether a separate AIQT orchestrator splits from the
  grc_library orchestrator.
- **Acceptance:** worked as scoped sub-items; the last umbrella item by maintainer direction.
