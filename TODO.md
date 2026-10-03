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

The row is `| ID | Status | Item | Tags |`. The `Status` cell shows how far the item has progressed, in
plain words:

- `In progress (#NNN)`: work on the item is under review in the named open pull requests.
- `Partly done`: part of the item's work is merged; the rest is still open.
- `Planned`: planning or design has started; no implementation is merged or under review yet.
- `Not started`: queued; no work has started.

The `Item` cell carries a one-line title with its `(severity, effort)` tag. The `Tags` cell carries
`[public]` (every row in this file) plus any of these tags: `[tooling]` the pack's own tools and
the OPF (operational files) framework; `[adopter]` affects projects that adopt the pack; `[future]` an idea under
consideration, no commitment; `[release]` a step in cutting a release; `[1.1.1]` must merge before the release 1.1.1 cut; `[docs]` a documentation change;
`[corpus]` the pack's rule content; `[BLOCKED: ...]` waiting on the named dependency or decision.

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
| OPF-HOMES-ACTIVATION-CHECKLIST | Partly done | Items that must land before the second OPF store layout activates (H, M) | `[public]` `[tooling]` |
| MODE-CANONICAL-SOURCE | Not started | Hooks that depend on the operating mode can read it from an optional configured file, not only from a hand-edited session record (H, S) | `[public]` `[tooling]` |
| STAMP-ONELINE | In progress (#391) | The stamp-truth-stop preview hook prints one-line messages (at most 100 characters) with no duplicate copy on standard error (M, S) | `[public]` `[tooling]` |
| FIX-MAIN-UNCOND | In progress (#385) | OPF tool self-tests run only for the exact self-test argument (M, M) | `[public]` `[tooling]` |
| FVC-FIXES | In progress (#387) | Three confirmed defects: upgrade lock release, descriptor ownership, generated-source docstrings (M, S) | `[public]` `[tooling]` |
| REQ-OPF-IMPORT-DECISIONS | Not started | OPF adoption splits a mixed decisions register into pending and decided records, preserving history (H, M) | `[public]` `[tooling]` |

## Priority 2 - Fill significant gaps

Fill significant gaps: deepen thin-but-present capability to operational sufficiency, and add the significant missing capabilities.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| GD-152 | Not started | Portable consumer-side findings-lifecycle capability (queue, resurface, escalation) (M, L) | `[public]` |
| PYTHON-FLOOR | In progress (#386) | Python 3.14 floor: one declared source for the minimum version and a check that enforces it (M, S) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U2 | Not started | Python 3.14 floor: OPF subtree (M, S) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U3 | Not started | Python 3.14 floor: core hook (M, S) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U4 | Not started | Python 3.14 floor: preview hooks (M, S) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U5 | Not started | Python 3.14 floor: adopter-run tools and remaining tools (M, M) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U6 | Not started | Python 3.14 floor: declarations in the docs and site (M, S) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U7 | Not started | Python 3.14 floor: release note in the 1.1.1 release cut (M, XS) | `[public]` `[adopter]` `[1.1.1]` |
| AIQT-INSTRUCTION-BUDGET | Not started | Keep the pack's always-loaded instructions within the decided size budget by moving rule detail into the on-demand layer (H, L) | `[public]` |
| AIQT-INSTRUCTION-BUDGET-GATE | In progress (#389) | Instruction size gate: a check that keeps the pack's always-loaded instructions within a set size (H, M) | `[public]` |
| AIQT-INSTRUCTION-BUDGET-DETAIL | In progress (#392) | Rule detail layer: a place in each rule's source for detail that is loaded only on demand (H, M) | `[public]` |
| TOOL-MERGE-TRAIN | In progress (#390) | Merge-train tool: after each merge, bring every open pull request up to date with main, regenerate the generated release files, rerun its checks and push the result (M, S) | `[public]` `[tooling]` |
| ENFORCEMENT-ATTR-SOURCE-DOC | Not started | Name --attr-source among the git value-taking options in the commit-identity hook description (L, S) | `[public]` `[docs]` |
| PUBLIC-ROADMAP-REFRESH | In progress (#395) | Bring the public roadmap up to date: plain progress words and a row for every open pull request (M, S) | `[public]` `[docs]` |

## Priority 3 - Tooling

Tooling: the pack's operational-files framework, migration and adoption machinery, and internal apparatus.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| PREVIEW-FLEET-PORT | Planned | Port ten generally useful hooks to the .preview/ channel (M, L) | `[public]` `[tooling]` |
| OPF-INIT | Partly done | `opf init`: initialize and wire OPF into a project, with staging and a mutation lock (H, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-INIT-LATER | Not started | Finish OPF project initialization: the later steps still open, such as git staging, publication and the command-line verbs (H, L) | `[public]` `[tooling]` |
| OPF-DOGFOOD | Planned | Move this project's own operational records into OPF (M, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-ENTRY | Planned | adoption entry point: a generated assistant-readable entry (llms.txt-style) for self-adoption (M, M) | `[public]` `[tooling]` |
| OPF-ADOPT-VALIDATE | Planned | adoption-validation harness: a standing "does it work" check for adopters (M, M) | `[public]` `[tooling]` |
| OPF-EARLYCUT | Planned | Early release of clean-start OPF adoption for first adopters (M, S) | `[public]` `[tooling]` |
| OPF-ENFORCE-PACK | Partly done | OPF enforcement pack: CI and pre-commit checks, plus hooks for each supported coding assistant that block direct record edits, with the known gaps published (H, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-PROMPT-PACK | Partly done | OPF prompt pack: adoption instructions, then post-adoption import instructions with an example for every type (M, S) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-IMPORTED-SERIES | Planned | OPF imported records: a relaxed but validated schema, imported IDs, doctor checks, and a firewall so history never satisfies current approvals (H, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-RECORD-IMPORT | Planned | OPF record import: a write mode for batches of historical records (H, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-IMPORT-COMPLETION | Planned | OPF import completion: prompt, status and verify modes with a per-source completion check (M, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-CONSUMER | Planned | migrate our own tooling to consume the OPF store (M, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-ORCH | Partly done | OPF adoption component: investigate a project, propose a plan, take one approval, then apply it deterministically (H, XL) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U2 | Not started | Adoption apply: create, move and retire file operations (M, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U3 | Not started | Adoption apply: create the project's OPF store with the same initialization code `opf init` uses (M, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U4 | Not started | Adoption apply: register unmanaged files and repoint consumers (M, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U5 | Not started | Adoption apply: add the verified pack files, generate the readable views and record the adoption (M, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U7 | Not started | Adoption: wire the enable-hook operation (M, S) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U10 | Not started | Adoption command: run the steps from investigating the project to applying the plan, and record the one approval (M, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U11 | Not started | Adoption completion evaluator: deterministic checks (M, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U12 | Not started | Adoption completion: check that direct record edits are blocked and the approved write paths still work (M, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U13 | Not started | Adoption: retire old files only after every completion check passes, and safely replace a file already at the destination (M, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-CONTRIBUTION-TYPE | In progress (#388) | OPF outbound contribution ledger type: append/write path (M, M) | `[public]` |
| VER-1 | Partly done | Adoption + versioning umbrella: release/tag process, drift gate, adopter manifest (H, XL) | `[public]` |
| REL-1 | Planned | Cut release 1.1.1: version tag, published digest and short changelog (H, M) | `[public]` `[release]` `[1.1.1]` `[BLOCKED: waits for the other items marked 1.1.1 to merge]` |
| REL-111-DECLARE | Not started | Release 1.1.1 cut change: declare the cut, add the changelog entry, set the version, and retire 1.1.0: remove it from IN_DEVELOPMENT_VERSIONS in tools/check_site_versions.py, update the site and disclosure text that names 1.1.0, and change the 754 born-release rows in .aiqt/core/id-history.toml from 1.1.0 to 1.1.1 (H, S) | `[public]` `[release]` `[1.1.1]` |
| REL-111-PUBLISH | Not started | Release 1.1.1: regenerate the manifest, record the artifact digests and publish the release digest (H, S) | `[public]` `[release]` `[1.1.1]` |
| REL-111-ATTEST | Not started | Release 1.1.1: post-tag attestation entry (H, XS) | `[public]` `[release]` `[1.1.1]` |
| ADVISORY-GATES-REBUILD | Not started | Rebuild the warning-only (advisory) checks on current main, after the 1.1.1 release (M, L) | `[public]` `[tooling]` |
| EN-2 | Planned | New protective hooks: trojan-source detection, self-guard loop, config-surface guard (M, L) | `[public]` |
| EN-5 | Partly done | Prose-enforcement hook roster: publish/enable the plugin for adopters (H, M) | `[public]` |
| S4 | Planned | Credential-destroy enforcement hook (M, L) | `[public]` `[adopter]` `[BLOCKED: shared shell-lexer dependency]` |
| WAIT-UTIL-RULE | Not started | A rule and hooks so an assistant keeps doing ready work while it waits for a result (redesigned, after the 1.1.1 release) (H, L) | `[public]` `[corpus]` |
| OPF-CUSTOM-FILES | Not started | OPF custom (adopter-defined) operational-file types (M, M) | `[public]` `[tooling]` |

## Priority 4 - Adopter experience

Adopter experience: capability and guidance for organizations adopting the pack. Scheduled deliberately, after the fix/gap/tooling tiers.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| OPF-ESTATE-RETIRE | Partly done | Retire the old OPF import and ingest code, keeping the readers that existing import evidence needs (M, L) | `[public]` `[tooling]` |
| OPF-ESTATE-RETIRE-ALLOC | In progress (#393) | Delete the unused OPF allocation module and its self-test (M, S) | `[public]` `[tooling]` |
| OPF-ESTATE-RETIRE-LF | In progress (#394) | New OPF stores no longer get the legacy fragment counter; an existing one is still accepted (M, S) | `[public]` `[tooling]` |
| PACK-HOOKS-PROMOTE | Not started | Promote the .preview/ hooks into the pack's hook set (M, L) | `[public]` `[adopter]` |
| DEV-1 | Planned | development-assistant install skill: per-agent file generation, setup wizard, install doctor (H, L) | `[public]` `[adopter]` |
| DEV-2 | Planned | adopter QA/review skill (M, M) | `[public]` `[adopter]` |
| TOOL-1 | Planned | `/aiqt` manager and external tooling catalog (M, M) | `[public]` `[adopter]` |
| OPF-STANDARDIZE | Planned | operational-files standardization for adopters (M, L) | `[public]` `[adopter]` |
| ADOPT-TEMPLATE | Not started | adopter guardrail-seed submission template (L, S) | `[public]` `[adopter]` |
| DOC-CNTDEF | Not started | Adopter guidance: copy the continue-by-default rule file into your project and point your configuration at it; do not paraphrase it (L, S) | `[public]` |
| DOC-RECORDS-STORE | Not started | Adopter guidance: keeping operational records in a separate private repository beside the code repository (M, S) | `[public]` |
| SITE-REDESIGN | Not started | Website: carry the last pending copy edits onto the current pages (L, S) | `[public]` `[docs]` |
| FIXGUARD-NONVACUITY-GATE | Not started | CI gate: each fix-guard test must really fail on the merge-base code (H, M) | `[public]` `[tooling]` |

## Priority 5 - Future direction

Future direction: ideas under consideration, no commitment yet. Picked deliberately, never from the routine queue.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| PHANTOM-WAIT-LAYER-A | Not started | Idea: record confirmed wake-ups so a stop check can tell a real wait from a claimed one (FYI, M) | `[public]` `[future]` |
| TEAMS-1 | Not started | Teams web console: governance activity across projects without a terminal (idea; no commitment) (FYI, XL) | `[public]` `[future]` |
| ENT-1 | Not started | Enterprise management: central policy, shared configuration, cross-team reporting (idea; no commitment) (FYI, XL) | `[public]` `[future]` |
| CWE-RULE-SPLITS | Not started | Review six suggested security-rule splits (M, M) | `[public]` `[corpus]` |

---

## Notes on maintenance

- Add new items at the appropriate band; a row's position is its queue order. Items move between bands
  as context changes (the id never changes).
- When an item is completed, its index row is removed here (no strikethroughs, no `[done]` suffixes).
- This file is the source of truth for what's queued; conversation history is not.
