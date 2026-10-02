# TODO

Forward-looking backlog of planned enhancements for the guardrails AIQT pack. This file is an
index-only roadmap (one row per open item, in priority bands); the per-item detail lives in the
maintainer's private backlog, joined by the stable id, and is not part of this
public repository (the detail does not need public visibility). Items are added when identified and
rotated out when completed. Historical change detail lives in [`CHANGELOG.md`](CHANGELOG.md).

This file is informational and is not subject to the pack's ordinary content, metadata, or
version-tracking conventions: the ordinary content and metadata gates skip it. It is a roadmap index,
not shippable corpus.

**This file is GENERATED from the maintainer's private backlog; do not edit it by hand.**

---

## How items are numbered and formatted

Items are grouped into five priority bands by work type: **P1** fix errors and prevent recurrence;
**P2** fill significant gaps; **P3** tooling; **P4** adopter experience; **P5** future direction.
Within a band, rows run in a deliberate order; a row's **position** is the queue order, not its number.

The row is `| ID | Status | Item | Tags |`. The `Status` cell shows how far the item has progressed:
`-` = queued, not yet started; `Seed` = design inputs gathered; `Plan` = an implementation plan exists;
`Impl` = an implementation draft exists. When work is under review, the cell adds `[in progress: #NNN]`
naming the open pull requests. The `Item` cell carries a one-line title with its `(severity, effort)`
tag; `Tags` carries list-membership plus any `[tooling]`/`[adopter]`/`[future]`/`[BLOCKED: ...]` tag.

**The id is a permanent identity, never recycled, and decoupled from the display band:** an item keeps
its id when it rebands, so one id maps to exactly one item across the whole history of the file. A
closed item's id retires with it and is never reused; items are never renumbered when the file is
reorganized.

**Effort scale**: **XS** single-line / single-cell; **S** single-section add; **M** multi-file bounded;
**L** new artefact + propagation; **XL** new domain / pack-wide reshape (may split). Severity is
`H[critical]` / `H` / `M` / `L` / `FYI`.

---

## Priority 1 - Fix errors and prevent recurrence

Fix errors and prevent their recurrence. Worked first.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| OPF-HOMES-ACTIVATION-CHECKLIST | - | Items that must land before the second OPF store layout activates (H, M) | `[public]` `[tooling]` |
| MODE-CANONICAL-SOURCE | - | Mode-gated hooks read an optional canonical mode file before the hand-synced lease field (H, S) | `[public]` `[tooling]` |
| FIX-MAIN-UNCOND | - [in progress: #385] | OPF tool self-tests run only for the exact self-test argument (M, M) | `[public]` `[tooling]` |
| FVC-FIXES | - [in progress: #387] | Three confirmed defects: upgrade lock release, descriptor ownership, generated-source docstrings (M, S) | `[public]` `[tooling]` |
| REQ-OPF-IMPORT-DECISIONS | - | OPF adoption splits a mixed decisions register into pending and decided records, preserving history (H, M) | `[public]` `[tooling]` |

## Priority 2 - Fill significant gaps

Fill significant gaps: deepen thin-but-present capability to operational sufficiency, and add the significant missing capabilities.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| GD-152 | - | Portable consumer-side findings-lifecycle capability (queue, resurface, escalation) (M, L) | `[public]` |
| PYTHON-FLOOR | - [in progress: #386] | Declare Python 3.14 as the minimum supported version, with a check and a release note (M, S) | `[public]` `[adopter]` |
| PYTHON-FLOOR-U2 | - | Python 3.14 floor: OPF subtree (M, S) | `[public]` `[adopter]` |
| PYTHON-FLOOR-U3 | - | Python 3.14 floor: core hook (M, S) | `[public]` `[adopter]` |
| PYTHON-FLOOR-U4 | - | Python 3.14 floor: preview hooks (M, S) | `[public]` `[adopter]` |
| PYTHON-FLOOR-U5 | - | Python 3.14 floor: adopter-run tools and remaining tools (M, M) | `[public]` `[adopter]` |
| PYTHON-FLOOR-U6 | - | Python 3.14 floor: declarations in the docs and site (M, S) | `[public]` `[adopter]` |
| PYTHON-FLOOR-U7 | - | Python 3.14 floor: release note in the 1.1.1 release cut (M, XS) | `[public]` `[adopter]` |
| AIQT-INSTRUCTION-BUDGET | - [in progress: #389, #392] | Keep the pack's always-loaded instructions within a gated size budget, with detail moved to an on-demand layer (H, L) | `[public]` |
| JOURNAL-READ-CONTAINED-FD-LEAK | - | The journal reader leaks a directory descriptor when a close fails (H, M) | `[public]` `[tooling]` |
| ENFORCEMENT-ATTR-SOURCE-DOC | - | Name --attr-source among the git value-taking options in the commit-identity hook description (S, S) | `[public]` `[docs]` |

## Priority 3 - Tooling

Tooling: the pack's operational-files framework, migration and adoption machinery, and internal apparatus.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| PREVIEW-FLEET-PORT | Plan | Port ten generally useful hooks to the .preview/ channel (M, L) | `[public]` `[tooling]` |
| OPF-INIT | Impl | `opf init`: initialize and wire OPF into a project, with staging and a mutation lock (H, L) | `[public]` `[tooling]` |
| OPF-INIT-LATER | - | Finish OPF project initialization: the remaining initialization slices (H, L) | `[public]` `[tooling]` |
| OPF-DOGFOOD | Plan | dogfood: migrate this project's own operational records to OPF (M, M) | `[public]` `[tooling]` |
| OPF-ADOPT-ENTRY | Plan | adoption entry point: a generated assistant-readable entry (llms.txt-style) for self-adoption (M, M) | `[public]` `[tooling]` |
| OPF-ADOPT-VALIDATE | Plan | adoption-validation harness: a standing "does it work" check for adopters (M, M) | `[public]` `[tooling]` |
| OPF-EARLYCUT | Plan | Early release of clean-start OPF adoption for first adopters (M, S) | `[public]` `[tooling]` |
| OPF-ENFORCE-PACK | Plan | OPF enforcement pack: CI floor, pre-commit check, per-platform deny hooks and disclosed residuals (H, L) | `[public]` `[tooling]` |
| OPF-PROMPT-PACK | Plan | OPF prompt pack: adoption instructions, then post-adoption import instructions with an example for every type (M, S) | `[public]` `[tooling]` |
| OPF-IMPORTED-SERIES | Plan | OPF imported records: a relaxed but validated schema, imported IDs, doctor checks, and a firewall so history never satisfies current approvals (H, L) | `[public]` `[tooling]` |
| OPF-RECORD-IMPORT | Plan | OPF record import: a write mode for batches of historical records (H, M) | `[public]` `[tooling]` |
| OPF-IMPORT-COMPLETION | Plan | OPF import completion: prompt, status and verify modes with a per-source completion check (M, M) | `[public]` `[tooling]` |
| OPF-CONSUMER | Plan | migrate our own tooling to consume the OPF store (M, M) | `[public]` `[tooling]` |
| OPF-ADOPT-ORCH | Impl | OPF adoption component: investigate a project, propose a plan, take one approval, then apply it deterministically (H, XL) | `[public]` `[tooling]` |
| OPF-ADOPT-U2 | - | Adoption apply: create, move and retire file operations (M, L) | `[public]` `[tooling]` |
| OPF-ADOPT-U3 | - | Adoption apply: initialize the store over the shared initialization substrate (M, M) | `[public]` `[tooling]` |
| OPF-ADOPT-U4 | - | Adoption apply: register unmanaged files and repoint consumers (M, M) | `[public]` `[tooling]` |
| OPF-ADOPT-U5 | - | Adoption apply: plant governance, render views and record the adoption (M, L) | `[public]` `[tooling]` |
| OPF-ADOPT-U7 | - | Adoption: wire the enable-hook operation (M, S) | `[public]` `[tooling]` |
| OPF-ADOPT-U10 | - | Adoption command: stage driver and approval capture (M, L) | `[public]` `[tooling]` |
| OPF-ADOPT-U11 | - | Adoption completion evaluator: deterministic checks (M, L) | `[public]` `[tooling]` |
| OPF-ADOPT-U12 | - | Adoption completion: wiring probes (M, M) | `[public]` `[tooling]` |
| OPF-ADOPT-U13 | - | Adoption: retirement gating and occupied-destination cutover (M, M) | `[public]` `[tooling]` |
| OPF-CONTRIBUTION-TYPE | - [in progress: #388] | OPF outbound contribution ledger type: append/write path (M, M) | `[public]` |
| VER-1 | - | Adoption + versioning umbrella: release/tag process, drift gate, adopter manifest (H, XL) | `[public]` |
| REL-1 | Plan | Cut release 1.1.1: version tag, published digest and short changelog (H, M) | `[public]` `[release]` `[BLOCKED: merge-wave + cut timing]` |
| REL-111-DECLARE | - | Release 1.1.1 cut change: declare the cut, add the changelog entry and set the version (H, S) | `[public]` `[release]` |
| REL-111-PUBLISH | - | Release 1.1.1: regenerate the manifest, record the artifact digests and publish the release digest (H, S) | `[public]` `[release]` |
| REL-111-ATTEST | - | Release 1.1.1: post-tag attestation entry (H, XS) | `[public]` `[release]` |
| EN-2 | Plan | New protective hooks: trojan-source detection, self-guard loop, config-surface guard (M, L) | `[public]` |
| EN-5 | Plan | Prose-enforcement hook roster: publish/enable the plugin for adopters (H, M) | `[public]` |
| S4 | Plan | Credential-destroy enforcement hook (M, L) | `[public]` `[adopter]` `[BLOCKED: shared shell-lexer dependency]` |
| OPF-CUSTOM-FILES | - | OPF custom (adopter-defined) operational-file types (M, M) | `[public]` `[tooling]` |

## Priority 4 - Adopter experience

Adopter experience: capability and guidance for organizations adopting the pack. Scheduled deliberately, after the fix/gap/tooling tiers.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| PACK-HOOKS-PROMOTE | - | Promote the .preview/ hooks into the pack's hook set (M, L) | `[public]` `[adopter]` |
| DEV-1 | Plan | development-assistant install skill: per-agent file generation, setup wizard, install doctor (H, L) | `[public]` `[adopter]` |
| DEV-2 | Plan | adopter QA/review skill (M, M) | `[public]` `[adopter]` |
| TOOL-1 | Plan | `/aiqt` manager and external tooling catalog (M, M) | `[public]` `[adopter]` |
| OPF-STANDARDIZE | Impl | operational-files standardization for adopters (M, L) | `[public]` `[adopter]` |
| ADOPT-TEMPLATE | - | adopter guardrail-seed submission template (S, S) | `[public]` `[adopter]` |
| DOC-CNTDEF | - | Adopter guidance: vendor-and-point the continue-by-default rule file, don't paraphrase (L, S) | `[public]` |
| DOC-RECORDS-STORE | - | Adopter guidance: out-of-tree records store (companion_stores / rooted-session) (M, S) | `[public]` |
| FIXGUARD-NONVACUITY-GATE | - | CI gate: each fix-guard test must really fail on the merge-base code (H, M) | `[public]` `[tooling]` |

## Priority 5 - Future direction

Future direction: ideas under consideration, no commitment yet. Picked deliberately, never from the routine queue.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| TEAMS-1 | - | Teams web console: governance activity across projects without a terminal (idea; no commitment) | `[public]` `[future]` |
| ENT-1 | - | Enterprise management: central policy, shared configuration, cross-team reporting (idea; no commitment) | `[public]` `[future]` |
| CWE-RULE-SPLITS | - | Review six suggested security-rule splits (M, M) | `[public]` `[corpus]` |

---

## Notes on maintenance

- Add new items at the appropriate band; a row's position is its queue order. Items move between bands
  as context changes (the id never changes).
- When an item is completed, its index row is removed here (no strikethroughs, no `[done]` suffixes).
- This file is the source of truth for what's queued; conversation history is not.
