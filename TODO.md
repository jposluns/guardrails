# guardrails TODO

The ordered backlog for the AIQT guardrails project. Transcribed 2026-08-12 from the grc_library
decoupling master plan (`decoupling-master-plan.md` §6, "Rebuilt guardrails TODO"), the plan of
record for the grc_library -> jposluns/guardrails decouple. Provenance ids (ORIGIN) trace each row
back to its originating grc_library P-TODO item; they are provenance only, never guardrails identity.

## Conventions

- Priorities are P1-P4 only. No P5.
  - **P1:** launch-critical fixes, urgent maintainer needs, the chat skill, site maturity (ROADMAP M1).
  - **P2:** project progress, the developer-assistant pack, planned guardrails (M2/M3).
  - **P3:** new guardrails intake, submitted PRs per spec and accepted grc_library defect/guardrail
    packages. A genuine guard-defect package moves to P1 as a fix.
  - **P4:** future or incompletely scoped work, especially community work.
- Keys are ordering only, banded to avoid colliding with grc_library id strings: P1 in 1.x, P2 in
  2.x, P4 in 4.x. No key string equals a cited grc_library id.
- One live row is one closeable action, normally one PR. Research-only work closes through the PR
  that records or consumes its decision.
- On closure, delete the live row and write a DONE record keyed by the closing PR. Imported source
  ids remain as provenance (ORIGIN), never as guardrails identity.
- Versioning: pack release version is SemVer; the first public release is 1.0.0 (chat-assistant
  capability), 1.1.0 the development-assistant capability. Per-file Date (UTC) for currency.

## P1, launch-critical

| Order | Work | ORIGIN |
| --- | --- | --- |
| 1.0.0 | Cost tier rule | 1.26.9 |
| 1.1.0 | Human oversight and autonomy threshold rule | 1.26.11 |
| 1.2.0 | NIST AI RMF and ISO/IEC 42001 framework symmetry | 1.26.10 |
| 1.3.0 | Portable rule-quality audit and consider-instead improvements | P-1.36 rule slice |
| 1.4.0 | Universal AIQT chat skill | 1.26.6 |
| 1.5.0 | Conformance suite and normative contract | 1.26.7 |
| 1.6.0 | Naming, licence, and contribution terms | 1.26.21 + 1.26.22 |
| 1.7.0 | Disclosure and source-classification matrix with sign-off (grc P-1.18 SIGNED OFF 2026-08-12; carry the signed decision) | P-1.18 |
| 1.8.0 | Zip provenance, final manifest, frozen release snapshot; the first public release is cut here as SemVer 1.0.0 | 1.26.8 subset, 1.26.3, 1.26.5 final |
| 1.9.0 | Initial repository-public and aiqt.ai launch (runs the Phase 8 flip procedure; Pages per A7) | 1.26.20 |
| 1.10.0 | Site documentation and decision matrix | 1.26.35 |
| 1.11.0 | Tiered adoption and two-minute on-ramp | 1.26.13 |

(The site-documentation and on-ramp rows may resort ahead of the launch row at the Architect's choice.)

## P2, developer pack and planned progress

| Order | Work | ORIGIN |
| --- | --- | --- |
| 2.0.0 | Read-full-output extension to evidence-grounded completion (post-launch, EARLY P2, half-done; not launch-blocking) | P-1.32 |
| 2.1.0 | Vendor cost-path verification recorded in a consuming PR | P-1.34 |
| 2.2.0 | Vendor data-handling verification recorded in a consuming PR | P-1.35 |
| 2.3.0 | Plugin and connector guardrail assessment | P-1.26 |
| 2.4.0 | Deterministic coding-adapter generator and offline --check | adapter generator, ROADMAP M2 |
| 2.5.0 | Claude, Codex, ChatGPT, and editor adapter forms | adapter generator, ROADMAP M2 |
| 2.6.0 | Coding-assistant install and download presentation | extends 1.26.20 + 1.26.35 |
| 2.7.0 | Capability and compatibility matrix | 1.26.15 |
| 2.8.0 | Provenance-as-proof and sanitized caught-defect casebook (disclosure-gated) | 1.26.12 |
| 2.9.0 | Dogfooding outcome scorecard (disclosure-gated) | 1.26.19 |
| 2.10.0 | AIQT threat model and responsible disclosure, full product scope | 1.26.16 |
| 2.11.0 | Cautious certification ladder | 1.26.18 |
| 2.12.0 | AI incident response discipline | 1.26.17 |
| 2.13.0 | CleanLanguage as a separately sourced AIQT module | 1.26.46 |
| 2.14.0 | Guardrails-native hook and gate inventory, seeded by the migrated grc inventory | P-1.36 hook/gate slice |
| 2.15.0 | Waiting-word Stop hook, native rebuild keyed on OBSERVABLE runnable-work state (NOT the retired vocabulary/regex approach; grc #1471 vocabulary hook was RETIRED 2026-08-10 as fundamentally flawed, replacement in design) | seed of 1.26.48 (vocabulary), superseded design |
| 2.16.0 | Guardrails operational hooks batch with regressions | 1.26.50 |
| 2.17.0 | Guardrails attestation gates batch with regressions | 1.26.51 |
| 2.18.0 | Activity and configuration parity gate | 1.26.42 |
| 2.19.0 | Findings loop v1 | 1.26.32 |
| 2.20.0 | Full reproducible release provenance and rollback governance | 1.26.8 full |
| 2.21.0 | Remaining machinery consolidation phases | 1.26.1 remainder |
| 2.22.0 | ShareAlike contribution integration | 1.26.2 |
| 2.23.0 | AIQT logs and metrics | 1.26.34 |
| 2.24.0 | Three-layer adopter conventions | 1.26.36 |
| 2.25.0 | Guardrail identity and update mechanism (also mechanizes the grc pin-bump path) | 1.26.39 |
| 2.26.0 | Scheduled trigger class and adopter gates | 1.26.41 |
| 2.27.0 | Local second-opinion CLI | 1.26.33 |
| 2.28.0 | Static role-tiered report | 1.26.43 |
| 2.29.0 | Plan-corpus drift sweep and ownership | 1.26.24 |
| 2.30.0 | Operating modes discipline | 1.26.25 |
| 2.31.0 | Session continuity discipline | 1.26.26 |
| 2.32.0 | Opt-in update check | 1.26.27 |
| 2.33.0 | Session health and degradation awareness | 1.26.28 |
| 2.34.0 | Portable /sitrep skill | 3.142 |
| 2.35.0 | Citable CC BY-SA methodology and reference model | 4.31 |
| 2.36.0 | Port the reference-currency playbook, adapted to pack citations (LOW, M3+) | grc playbook |

## P3, new guardrails intake

No live migrated P3 row after PR #1471 finishes at M0. Two lanes: community-submitted guardrails
(PR per spec, once contribution conventions land and the repo is public) and grc_library guardrail
packages via the shared drop (rule + incident provenance). Create one row per accepted package at
the next sort position; close it with the guardrails PR. A genuine guard-defect package moves to P1
and, on release, triggers the grc pin-bump (the sole freeze exception). Never preallocate placeholders.

## P4, future and community

| Order | Work | ORIGIN |
| --- | --- | --- |
| 4.0.0 | Community failure-mode flywheel | 1.26.14 |
| 4.1.0 | Early pilot cohort | 1.26.23 |
| 4.2.0 | Reference Level 3 pilot | 1.26.37 |
| 4.3.0 | Promotion model panel | 1.26.38 |
| 4.4.0 | Contribution-back lifecycle and community intake | 1.26.40 |
| 4.5.0 | Community comms: forum, group, announcement channels | new |
| 4.6.0 | Additional families, hosted concepts; sequenced last | 1.26.45 |
