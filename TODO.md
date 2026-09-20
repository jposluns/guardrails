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

The row is `| ID | Item | Tags |`. The `Item` cell carries a one-line title with its `(severity, effort)`
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

| ID | Item | Tags |
| --- | --- | --- |
| U5-CHANGELOG-PARSE-FIX | Fail-closed changelog heading scanner: cannot-evaluate on ambiguous Markdown blocks (M, S) | `[public]` |

## Priority 2 - Fill significant gaps

Fill significant gaps: deepen thin-but-present capability to operational sufficiency, and add the significant missing capabilities.

| ID | Item | Tags |
| --- | --- | --- |
| GD-152 | Portable consumer-side findings-lifecycle capability (queue, resurface, escalation) (M, L) | `[public]` |

## Priority 3 - Tooling

Tooling: the pack's operational-files framework, migration and adoption machinery, and internal apparatus.

| ID | Item | Tags |
| --- | --- | --- |
| OPF-INIT | `opf init`: initialize and wire OPF into a project, with staging and a mutation lock (H, L) | `[public]` `[tooling]` `[BLOCKED: design sign-off]` |
| OPF-DOGFOOD | dogfood: migrate this project's own operational records to OPF (M, M) | `[public]` `[tooling]` |
| OPF-ADOPT-ENTRY | adoption entry point: a generated assistant-readable entry (llms.txt-style) for self-adoption (M, M) | `[public]` `[tooling]` |
| OPF-ADOPT-VALIDATE | adoption-validation harness: a standing "does it work" check for adopters (M, M) | `[public]` `[tooling]` |
| OPF-EARLYCUT | early release exposing `opf migrate` to first adopters (M, S) | `[public]` `[tooling]` |
| OPF-CONSUMER | migrate our own tooling to consume the OPF store (M, M) | `[public]` `[tooling]` |
| OPF-WRITER | OPF round-trip record-append path (M, M) | `[public]` `[BLOCKED: writer licence review]` |
| OPF-DECISIONS-REGISTER | OPF decisions register: file answered decisions and preference patterns (M, M) | `[public]` `[BLOCKED: round-trip writer]` |
| OPF-D2B-IGNORE-COMPLETE | `opf init` global-config-aware ignore-destination refusal (M, M) | `[public]` |
| OPF-CONTRIBUTION-TYPE | OPF outbound contribution ledger type: append/write path (M, M) | `[public]` `[BLOCKED: round-trip writer]` |
| OPF-CHANGELOG-ABSORB | OPF changelog type: TOML source with a generated CHANGELOG.md view (M, M) | `[public]` |
| VER-1 | Adoption + versioning umbrella: release/tag process, drift gate, adopter manifest (H, XL) | `[public]` |
| EN-2 | New protective hooks: trojan-source detection, self-guard loop, config-surface guard (M, L) | `[public]` |
| EN-5 | Prose-enforcement hook roster: publish/enable the plugin for adopters (H, M) | `[public]` |
| S4 | Credential-destroy enforcement hook (M, L) | `[public]` `[BLOCKED: capability review]` |
| OPF-CUSTOM-FILES | OPF custom (adopter-defined) operational-file types (M, M) | `[public]` `[tooling]` |

## Priority 4 - Adopter experience

Adopter experience: capability and guidance for organizations adopting the pack. Scheduled deliberately, after the fix/gap/tooling tiers.

| ID | Item | Tags |
| --- | --- | --- |
| DEV-1 | development-assistant install skill: per-agent file generation, setup wizard, install doctor (H, L) | `[public]` `[adopter]` |
| DEV-2 | adopter QA/review skill (M, M) | `[public]` `[adopter]` |
| TOOL-1 | `/aiqt` manager and external tooling catalog (M, M) | `[public]` `[adopter]` |
| OPF-STANDARDIZE | operational-files standardization for adopters (M, L) | `[public]` `[adopter]` |
| ADOPT-TEMPLATE | adopter guardrail-seed submission template (S, S) | `[public]` `[adopter]` |
| DOC-CNTDEF | Adopter guidance: vendor-and-point the continue-by-default rule file, don't paraphrase (L, S) | `[public]` |
| DOC-RECORDS-STORE | Adopter guidance: out-of-tree records store (companion_stores / rooted-session) (M, S) | `[public]` |

## Priority 5 - Future direction

Future direction: ideas under consideration, no commitment yet. Picked deliberately, never from the routine queue.

| ID | Item | Tags |
| --- | --- | --- |
| TEAMS-1 | Teams web console: governance activity across projects without a terminal (idea; no commitment) | `[public]` `[future]` |
| ENT-1 | Enterprise management: central policy, shared configuration, cross-team reporting (idea; no commitment) | `[public]` `[future]` |

---

## Notes on maintenance

- Add new items at the appropriate band; a row's position is its queue order. Items move between bands
  as context changes (the id never changes).
- When an item is completed, its index row is removed here (no strikethroughs, no `[done]` suffixes).
- This file is the source of truth for what's queued; conversation history is not.
