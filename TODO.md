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
consideration, no dates yet; `[release]` a step in cutting a release; `[1.1.1]` must merge before the
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
| FIX-SITE-FAMILY-FOOTER-7 | Not started | aiqt.ai and opfiles.ai footers lack the seventh project card and load assets from the draft path (S, S) | `[public]` |
| MODE-CANONICAL-SOURCE | Partly done | Hooks that depend on the operating mode read both a configured canonical mode file and the session record, and treat the mode as unattended when either says so (reading one configured file already works) (H, S) | `[public]` `[tooling]` |
| FIX-411-REBUILD-CHILD-CONTRACT | Planned | Gates run loaded code in a child under the shared fail-closed contract (M, M) | `[public]` `[tooling]` |

## Priority 2 - Fill significant gaps

Fill significant gaps: strengthen capabilities that exist but are too thin to rely on in practice, and add the significant capabilities that are missing.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| MAPPINGS-FIT-SWEEP | Not started | Published framework mappings: remove or downgrade weak and over-labelled fits, then judge the rest (M, M) | `[public]` `[corpus]` |
| GD-152 | Not started | A portable way for an adopting project to track review findings to an outcome: queue them, bring overdue ones back at the start of a session, and escalate any left unhandled (M, L) | `[public]` |
| PYTHON-FLOOR-U7 | Not started | Python 3.14 floor: the release note, written into the changelog in step 1 of the 1.1.1 cut (M, XS) | `[public]` `[adopter]` `[release]` `[1.1.1 cut]` |
| AIQT-INSTRUCTION-BUDGET | In progress (#392) | Cut the pack's instructions that load in every session from 112,222 to at most 90,000 characters: decide where each rule's detail belongs, move that detail into the layer loaded only on demand, then make the size check enforce the 90,000 limit (H, L) | `[public]` |
| TOOL-MERGE-TRAIN | In progress (#390) | Merge-train tool: after each merge, bring every open pull request up to date with main, regenerate the generated release files, rerun its checks and push the result (M, S) | `[public]` `[tooling]` |
| SEED-TRACKER-LABEL-ADOPTER-SHAPES | Not started | Adopters declare their own work-label shapes for the internal-names gate, with an added-lines mode for review packs (M, M) | `[public]` `[adopter]` |

## Priority 3 - Tooling

Tooling: the pack's operational-files framework, migration and adoption machinery, and internal apparatus.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| PREVIEW-FLEET-PORT | Planned | Port ten more generally useful hooks to the .preview/ channel, separate from the hooks already published there (M, L) | `[public]` `[tooling]` |
| VER-1 | Partly done | Release and version process for adopting projects: the remaining steps for tagging releases so adopters can pin a version and verify it (the core version checks are merged) (H, XL) | `[public]` |
| REL-1 | Planned | Cut release 1.1.1 (version tag, published digest and short changelog) in seven steps, the rows that follow, done in order (H, M) | `[public]` `[release]` `[1.1.1 cut]` `[BLOCKED: waits for every item tagged 1.1.1 (not 1.1.1 cut) to merge]` |
| REL-111-DECLARE | Planned | Release 1.1.1 cut, step 1, before the freeze: declare the cut, add the changelog entry and set the version; retire 1.1.0, which is skipped, by checking each feature that public text promises for 1.1.0 against main, crediting to 1.1.1 what main ships and moving the rest to 1.2.0 (the development assistant's setup wizard, install doctor and per-agent files are decided for 1.2.0; still to check: the enforcement controls in README.md:18 and site/index.html:145, the review tiers in disclosure.toml:77, the deeper per-language security in disclosure.toml:47, the repository records in site/draft-src/teams.toml (which generates site/teams/index.html), the option to send a new guardrail back to the project as a pull request in site/tech-details.html:167, and the rest of the 1.1.0 design in site/tech-details.html:66 and docs/development.md (which generates site/development.html)); set IN_DEVELOPMENT_VERSIONS in tools/check_site_versions.py to {"1.2.0"} and update its comment and self-test case; settle the 754 entries in .aiqt/core/id-history.toml that record 1.1.0 as the release in which an id first appeared (relabelling them in place fails the append-only history check, so the maintainer is choosing how they are kept); and update the matching text in README.md, ROADMAP.md, roadmap.toml, DISCLOSURE.md, disclosure.toml, the docs/ and site/ pages, tools/check_overclaim.py and .aiqt/core/gates/manifest.toml; also update the text that still names AIQT 1.0.5: disclosure.toml and roadmap.toml (which generate the matching text in site/disclosure.html, site/roadmap.html, DISCLOSURE.md and ROADMAP.md), docs/evidence.md and site/draft-src/teams.toml (which generate site/evidence.html and site/teams/index.html), and the hand-written text in site/install.html; and every other public mention of 1.0.5 as the current release, such as "chat assistant (1.0.5)" in docs/development.md and site/development.html, and "the 1.0.5 release" in the docs/ pages (H, L) | `[public]` `[release]` `[1.1.1 cut]` |
| REL-111-PUBLISH | Planned | Release 1.1.1 cut, step 2, one pull request (RELEASING.md steps 1 to 5): freeze the release content with every check passing, regenerate the manifest, record the artifact digests in changelog.toml, fill the Built from and Checksum fields on the evidence page, and confirm the artifact checksum check is armed and passing (the release files: the same two as 1.0.5, the skill zip and the instructions file, rebuilt for 1.1.1; the skill's own version number is raised for 1.1.1 (the new number is chosen at the cut), because the skill's licence text changed after 1.0.5 shipped, and RELEASING.md's file names and the version line of the three short instruction files (site/downloads/aiqt-instructions-8k.txt, -5k.txt and -1_5k.txt) follow it) (H, M) | `[public]` `[release]` `[1.1.1 cut]` |
| REL-111-QA-PRETAG | Not started | Release 1.1.1 cut, step 3, before tagging: QA, an independent review of the release candidate (the step 2 merge commit), passes and its result is recorded as an attestation (a stored record of the reviewers' pass verdicts, which the release record later identifies by its digest); then the pre-tag check (tools/check_release_build.py --pre-tag) passes, including the evidence a first release must carry (waits on the row that prepares it); every attestation time must be earlier than the tag's date (H, S) | `[public]` `[release]` `[1.1.1 cut]` |
| REL-111-TAG | Not started | Release 1.1.1 cut, step 4 (RELEASING.md step 6): apply the annotated tag v1.1.1 to the step 2 merge commit and push it (H, XS) | `[public]` `[release]` `[1.1.1 cut]` |
| REL-111-TAG-KEY | Not started | Release 1.1.1 cut, step 5 (RELEASING.md step 6), a second pull request: record the tag v1.1.1 in the release's changelog.toml entry, which arms the check that release tags only increase; waits on the row that reconciles the release steps and the post-tag check, which sets the final order of this step and the next (H, XS) | `[public]` `[release]` `[1.1.1 cut]` |
| REL-111-ATTEST | Not started | Release 1.1.1 cut, step 6, after tagging: add the release and its QA attestation (the recorded review result from step 3) to the record of released versions (.aiqt/core/releases.toml), which the post-tag check (tools/check_release_build.py --post-tag) verifies; waits on the row that reconciles the release steps and the post-tag check, which sets the final order of this step and the one before (H, XS) | `[public]` `[release]` `[1.1.1 cut]` |
| REL-111-ANNOUNCE | Not started | Release 1.1.1 cut, step 7, last: publish the release digest at its independent location and tell the adopters waiting for a named release its tag and digest (H, XS) | `[public]` `[release]` `[1.1.1 cut]` |
| ADVISORY-GATES-REBUILD | Not started | Rebuild the warning-only checks for timer restore and file-path classification, and the check that keeps their wording free of absolute guarantees, on current main after the 1.1.1 release (starting over: an earlier attempt, closed pull request #241, was not merged) (M, L) | `[public]` `[tooling]` |
| EN-2 | Planned | New protective hooks: detect hidden or look-alike Unicode characters that make code read differently from how it runs, turn a repeated assistant mistake into a new guardrail, and guard configuration changes (M, L) | `[public]` |
| EN-5 | Partly done | Hooks that enforce the pack's written rules automatically: publish the plugin that packages them for adopting projects, with instructions to install and turn it on (the hooks and a first version of the plugin are merged) (H, M) | `[public]` |
| S4 | Planned | A hook that asks for confirmation before a command deletes or overwrites credentials (M, L) | `[public]` `[adopter]` `[BLOCKED: waits for a shared component that reads shell commands the way the shell does]` |
| WAIT-UTIL-RULE | Not started | Hooks that enforce the rule on doing ready work while waiting for a result, a rule that already ships (starting over after the 1.1.1 release: an earlier attempt, closed pull request #206, was not merged) (H, L) | `[public]` `[corpus]` |

## Priority 4 - Adopter experience

Adopter experience: capability and guidance for organizations adopting the pack. Scheduled deliberately, after the fix/gap/tooling tiers.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| PACK-HOOKS-PROMOTE | Not started | Promote the .preview/ hooks into the pack's hook set (M, L) | `[public]` `[adopter]` |
| DEV-1 | Planned | Development assistant, planned for the 1.2.0 release: a skill that installs the pack into a project, with a setup wizard, a doctor command that checks the install, and the instruction file each coding assistant expects (H, L) | `[public]` `[adopter]` |
| DEV-2 | Planned | A skill that adopting projects can use to review work for quality (M, M) | `[public]` `[adopter]` |
| TOOL-1 | Planned | A `/aiqt` command to manage the pack in a project, and a catalog of outside tools that work with it, each reviewed before it is listed (M, M) | `[public]` `[adopter]` |
| ADOPT-TEMPLATE | Not started | A template for adopters to propose a new guardrail to the pack (L, S) | `[public]` `[adopter]` |
| DOC-CNTDEF | Not started | Adopter guidance: copy the continue-by-default rule file into your project and point your configuration at it; do not paraphrase it (L, S) | `[public]` |
| DOC-RECORDS-STORE | Not started | Adopter guidance: configuring the pack's hooks so an assistant may write to a separate private records repository beside the code repository (the operational-files specification already describes that repository layout) (M, S) | `[public]` |

## Priority 5 - Future direction

Future direction: planned work and ideas under consideration, no dates yet. Picked deliberately, never from the routine queue.

| ID | Status | Item | Tags |
| --- | --- | --- | --- |
| PHANTOM-WAIT-LAYER-A | Not started | Idea: record confirmed wake-ups so the check that runs when an assistant ends its turn can tell a real wait from a claimed one (starting over: an earlier version, closed pull request #190, was not merged) (FYI, M) | `[public]` `[future]` |
| TEAMS-1 | Not started | Teams web console: governance activity across projects without a terminal (planned; no dates yet) (FYI, XL) | `[public]` `[future]` |
| ENT-1 | Not started | Enterprise management: central policy, shared configuration, cross-team reporting (planned; no dates yet) (FYI, XL) | `[public]` `[future]` |
| CWE-RULE-SPLITS | Not started | Review six suggested security-rule splits (M, M) | `[public]` `[corpus]` |

---

## Notes on maintenance

- Items are added, updated and moved between bands in the maintainer's private backlog, and this file
  is regenerated from it; it is never edited by hand. A row's position is its queue order, and an item
  keeps its id when it moves.
- When an item is completed, it is closed in the backlog and its row drops from this file at the next
  regeneration (no strikethroughs, no `[done]` suffixes).
