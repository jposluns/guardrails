# TODO

Forward-looking backlog of planned enhancements for the guardrails AIQT pack. This file is an
index-only roadmap (one row per open item, in priority bands); the per-item detail lives in the
maintainer's private backlog, joined by the stable id, and is not part of this
public repository (the detail does not need public visibility). Items are added when identified and
rotated out when completed. Historical change detail lives in [`CHANGELOG.md`](CHANGELOG.md).

This file is for information only. It is not part of the rules the pack ships, so the pack's
automated checks on rule content, metadata and version numbers do not apply to it.

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
consideration, no commitment; `[release]` a step in cutting a release; `[1.1.1]` must merge before the
release 1.1.1 cut begins; `[1.1.1 cut]` a step of the release 1.1.1 cut itself, done in order at the cut
once every `[1.1.1]` item has merged; `[docs]` a documentation change; `[corpus]` the pack's rule
content; `[BLOCKED: ...]` waiting on the named dependency or decision.

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
| OPF-HOMES-ACTIVATION-CHECKLIST | Partly done | Work needed before OPF switches to its second layout for where store files live (OPF tools still write to the first layout): the remaining fixes, then a full end-to-end test (H, M) | `[public]` `[tooling]` |
| MODE-CANONICAL-SOURCE | Not started | Hooks that depend on the operating mode can read it from an optional configured file, not only from a hand-edited session record (H, S) | `[public]` `[tooling]` |
| STAMP-ONELINE | In progress (#391) | The stamp-truth-stop preview hook prints one-line messages (at most 100 characters) with no duplicate copy on standard error (M, S) | `[public]` `[tooling]` |
| FIX-MAIN-UNCOND | In progress (#385) | OPF tool self-tests run only for the exact self-test argument (M, M) | `[public]` `[tooling]` |
| FVC-FIXES | In progress (#387) | Three confirmed defects: upgrade lock release, descriptor ownership, generated-source docstrings (M, S) | `[public]` `[tooling]` |
| REQ-OPF-IMPORT-DECISIONS | Not started | OPF adoption splits a mixed decisions register into pending and decided records, preserving history (H, M) | `[public]` `[tooling]` |

## Priority 2 - Fill significant gaps

Fill significant gaps: deepen thin-but-present capability to operational sufficiency, and add the significant missing capabilities.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| GD-152 | Not started | A portable way for an adopting project to track review findings to an outcome: queue them, bring overdue ones back at the start of a session, and escalate any left unhandled (M, L) | `[public]` |
| PYTHON-FLOOR | In progress (#386) | Python 3.14 floor: one declared source for the minimum version and a check that enforces it (M, S) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U2 | Not started | Python 3.14 floor: OPF subtree (M, S) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U3 | Not started | Python 3.14 floor: core hook (M, S) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U4 | Not started | Python 3.14 floor: preview hooks (M, S) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U5 | Not started | Python 3.14 floor: adopter-run tools and remaining tools (M, M) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U6 | Not started | Python 3.14 floor: declarations in the docs and site (M, S) | `[public]` `[adopter]` `[1.1.1]` |
| PYTHON-FLOOR-U7 | Not started | Python 3.14 floor: the release note, written into the changelog in step 1 of the 1.1.1 cut (M, XS) | `[public]` `[adopter]` `[1.1.1 cut]` |
| AIQT-INSTRUCTION-BUDGET | Not started | Cut the pack's instructions that load in every session from 112,222 to at most 90,000 characters: decide where each rule's detail belongs, move that detail into the layer loaded only on demand, then make the size check enforce the 90,000 limit (H, L) | `[public]` |
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
| OPF-INIT | Partly done | `opf init` base: the shared setup code that creates a project's OPF store and that adoption reuses (store creation that resumes after an interruption, the first build of the readable views, and a lock against concurrent changes) (H, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-INIT-LATER | Not started | `opf init` after the base: the later steps, such as staging the created files in git, publishing them, finishing the run, and the command-line commands (not yet assigned to a release) (H, L) | `[public]` `[tooling]` |
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
| OPF-ADOPT-U4 | Not started | Adoption apply: list in the OPF manifest the files OPF leaves unmanaged, and update the files that refer to moved records so they point to the new locations (M, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U5 | Not started | Adoption apply: add the verified pack files, generate the readable views and record the adoption (M, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U7 | Not started | Adoption: wire the enable-hook operation (M, S) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U10 | Not started | Adoption command: run the steps from investigating the project to applying the plan, and record the one approval (M, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U11 | Not started | Adoption completion, part 1: automatic read-only checks that the result matches the approved plan, every file found was handled, retired files can be restored, and the new OPF store is valid (M, L) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U12 | Not started | Adoption completion: check that direct record edits are blocked and the approved write paths still work (M, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-ADOPT-U13 | Not started | Adoption: retire old files only after every completion check passes, and safely replace a file already at the destination (M, M) | `[public]` `[tooling]` `[1.1.1]` |
| OPF-CONTRIBUTION-TYPE | In progress (#388) | OPF record type for what a project sends to other projects (fixes, proposals, suggested guardrails) with proof of delivery: writing a record when something is sent (M, M) | `[public]` |
| VER-1 | Partly done | Release and version process for adopting projects: the remaining steps for tagging releases so adopters can pin a version and verify it (the core version checks are merged) (H, XL) | `[public]` |
| REL-1 | Planned | Cut release 1.1.1 (version tag, published digest and short changelog) in three steps, the rows that follow, done in order (H, M) | `[public]` `[release]` `[1.1.1 cut]` `[BLOCKED: waits for every item tagged 1.1.1 (not 1.1.1 cut) to merge]` |
| REL-111-DECLARE | Not started | Release 1.1.1 cut, step 1: declare the cut, add the changelog entry and set the version; retire 1.1.0, which is skipped: remove it from IN_DEVELOPMENT_VERSIONS in tools/check_site_versions.py, change the 754 born-release rows in .aiqt/core/id-history.toml from 1.1.0 to 1.1.1, and update every public file that calls 1.1.0 in development or promises features for it (README.md, ROADMAP.md, roadmap.toml, DISCLOSURE.md, disclosure.toml, the docs/ and site/ pages, and the matching text in tools/check_overclaim.py and .aiqt/core/gates/manifest.toml), moving the development assistant's promised features (setup wizard, install doctor, per-agent files) to 1.2.0 (H, S) | `[public]` `[release]` `[1.1.1 cut]` |
| REL-111-PUBLISH | Not started | Release 1.1.1 cut, step 2: regenerate the manifest, record the artifact digests, tag the release and publish the release digest (H, S) | `[public]` `[release]` `[1.1.1 cut]` |
| REL-111-ATTEST | Not started | Release 1.1.1 cut, step 3, after tagging: add the release to the record of released versions (.aiqt/core/releases.toml), which the post-tag check verifies (H, XS) | `[public]` `[release]` `[1.1.1 cut]` |
| ADVISORY-GATES-REBUILD | Not started | Rebuild the warning-only (advisory) checks on current main, after the 1.1.1 release (M, L) | `[public]` `[tooling]` |
| EN-2 | Planned | New protective hooks: detect hidden or look-alike Unicode characters that make code read differently from how it runs, turn a repeated assistant mistake into a new guardrail, and guard configuration changes (M, L) | `[public]` |
| EN-5 | Partly done | Hooks that enforce the pack's written rules automatically: publish them as a plugin that adopting projects can install and turn on (the hooks themselves are merged) (H, M) | `[public]` |
| S4 | Planned | A hook that asks for confirmation before a command deletes or overwrites credentials (M, L) | `[public]` `[adopter]` `[BLOCKED: waits for a shared component that reads shell commands the way the shell does]` |
| WAIT-UTIL-RULE | Not started | A rule and hooks so an assistant keeps doing ready work while it waits for a result (redesigned, after the 1.1.1 release) (H, L) | `[public]` `[corpus]` |
| OPF-CUSTOM-FILES | Not started | OPF custom (adopter-defined) operational-file types (M, M) | `[public]` `[tooling]` |

## Priority 4 - Adopter experience

Adopter experience: capability and guidance for organizations adopting the pack. Scheduled deliberately, after the fix/gap/tooling tiers.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| OPF-ESTATE-RETIRE | Partly done | Retire the old OPF import and ingest code, keeping the readers that existing import evidence needs (M, L) | `[public]` `[tooling]` |
| OPF-ESTATE-RETIRE-ALLOC | In progress (#393) | Delete the unused OPF allocation module and its self-test (M, S) | `[public]` `[tooling]` |
| OPF-ESTATE-RETIRE-LF | In progress (#394) | New OPF stores no longer get the ID counter of a deprecated record type that only the old import code used; stores that already have that counter stay valid (M, S) | `[public]` `[tooling]` |
| PACK-HOOKS-PROMOTE | Not started | Promote the .preview/ hooks into the pack's hook set (M, L) | `[public]` `[adopter]` |
| DEV-1 | Planned | Development assistant, planned for the 1.2.0 release: a skill that installs the pack into a project, with a setup wizard, a doctor command that checks the install, and the instruction file each coding assistant expects (H, L) | `[public]` `[adopter]` |
| DEV-2 | Planned | adopter QA/review skill (M, M) | `[public]` `[adopter]` |
| TOOL-1 | Planned | `/aiqt` manager and external tooling catalog (M, M) | `[public]` `[adopter]` |
| OPF-STANDARDIZE | Planned | operational-files standardization for adopters (M, L) | `[public]` `[adopter]` |
| ADOPT-TEMPLATE | Not started | adopter guardrail-seed submission template (L, S) | `[public]` `[adopter]` |
| DOC-CNTDEF | Not started | Adopter guidance: copy the continue-by-default rule file into your project and point your configuration at it; do not paraphrase it (L, S) | `[public]` |
| DOC-RECORDS-STORE | Not started | Adopter guidance: keeping operational records in a separate private repository beside the code repository (M, S) | `[public]` |
| SITE-REDESIGN | Not started | Website: carry the last pending copy edits onto the current pages (L, S) | `[public]` `[docs]` |
| FIXGUARD-NONVACUITY-GATE | Not started | CI check: a test added to guard a bug fix must fail when run on the code from before the fix, proving it would catch the bug coming back (H, M) | `[public]` `[tooling]` |

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

- Items are added, updated and moved between bands in the maintainer's private backlog, and this file
  is regenerated from it; it is never edited by hand. A row's position is its queue order, and an item
  keeps its id when it moves.
- When an item is completed, it is closed in the backlog and its row drops from this file at the next
  regeneration (no strikethroughs, no `[done]` suffixes).
