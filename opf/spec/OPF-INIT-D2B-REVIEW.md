# OPF-INIT-D2B-REVIEW

Durable sensitive-tier review register for OPF initialization.

| ID | Required invariant and evidence | Owner | Status |
|---|---|---|---|
| F01 | Wrong root/prefix/repository/worktree/common-dir/index cannot bind; nested and linked-worktree fixtures. | PR3/5 | specified - not runtime-verified |
| F02 | Nonrepo, bare, unsupported topology/platform/filesystem/index format, or unconfirmed target refuses. | PR2/3/5 | specified - not runtime-verified |
| F03 | Both pointers, alternate stores, retired tokens, malformed/ambiguous/partial adoption refuse. | PR3 | specified - not runtime-verified |
| F04 | A deletion present only in the worktree or index refuses fresh init; a proven committed deletion re-adopts only through the adoption observer, seeding counters from one pinned first-parent ancestral snapshot (never zero for a missing value, never a maximum over history). Reconciled with the PR3 ratification (PD-D2B-PR3-SCHEMA 6). | PR3a/4 | PR3a pinned-snapshot reader and worktree/index-deletion refusal runtime-verified in draft (_opf_init_operation self-test B1-B10, R6, R10); PR4 observer pending |
| F05 | Shallow/missing/unreadable/malformed/bounded-out history is cannot-evaluate; no lazy fetch. | PR3 | specified - not runtime-verified |
| F06 | Replacement refs, grafts, commit-graph effects, changed HEAD/binding, and broken refs cannot substitute observations. | PR3 | specified - not runtime-verified |
| F07 | Failed resolution is not unborn proof; valid symbolic HEAD and absent target are independently established. | PR3 | specified - not runtime-verified |
| F08 | Forged/stale provenance, missing publication evidence, or committed damage cannot authorize empty prior. | PR3/6 | specified - not runtime-verified |
| F09 | Hidden entries, empty directories, read errors, disappearing entries, and exhausted caps cannot disappear from inventory. | PR4 | specified - not runtime-verified |
| F10 | Links, special files, unsafe hardlinks, embedded repos/gitlinks, and replaced ancestors refuse unsafe access. | PR3-6 | specified - not runtime-verified |
| F11 | Closed decisions reject malformed fields, caps, duplicates, overlaps, ghosts, unused choices, and missing coverage. | PR1/4 | PR1 contract-layer runtime-verified (_opf_init_contract self-test); PR4 apply pending |
| F12 | Root/HEAD/content/mode/type/group changes invalidate acceptance before adoption. | PR1/4/6 | specified - not runtime-verified |
| F13 | Plans, proposals, report edits, and blanket consent cannot substitute for explicit attributed acceptance. | PR4/7 | specified - not runtime-verified |
| F14 | Managed/reserved destination collisions refuse at planning even with identical bytes or generated headers; only after the operation's durable intent may an exact bytes, mode, type, and single-link match on resume of the SAME operation be a plan-authorized dedupe. Reconciled with the PR3 ratification (PD-D2B-PR3-SCHEMA 3, 5). | PR1/3a/3b/4/6 | PR1 contract-layer runtime-verified (_opf_init_contract self-test); PR3a fresh-collision refusal and resume dedupe runtime-verified in draft (R6, R4); PR3b pre-intent view collision refusal and post-intent view dedupe runtime-verified in draft (V7, R4 view points); PR4/6 apply pending |
| F15 | After unmanaged adoption, no retained-body reads, staging, or widened exemptions. | PR4/6/7 | specified - not runtime-verified |
| F16 | Keep worklog entries are deterministic, consecutive, attributed infra; counters agree; no repeated allocation or invented partial import. | PR4/6 | specified - not runtime-verified |
| F17 | Incompatible changelog and VERSION in worktree/index/HEAD refuse D2b admission. | PR4 | specified - not runtime-verified |
| F18 | Publication races and partial/same-byte foreign files cannot bypass exclusive creation and ownership evidence: payloads are staged complete under a plan-recorded name and published by link without replacement; a strict prefix is refused, never repaired. Reconciled with the PR3 ratification (PD-D2B-PR3-SCHEMA 3). | PR3a/3b/6 | PR3a source publication runtime-verified in draft (R4 prelink/postlink, R5 strict-prefix); PR3b view publication through the same primitives runtime-verified in draft (R4 view prelink/postlink, V6 strict-prefix); PR6 pending |
| F19 | Only exact S file entries enter the index; no expansion, conflict stages, intent-to-add, or V/K/E/C. | PR5 | specified - not runtime-verified |
| F20 | Unrelated index entries/flags/metadata survive; unsupported extensions/formats and corrupt indexes refuse. | PR5 | specified - not runtime-verified |
| F21 | Git configuration, filters, encodings, helpers, hooks, fsmonitor, tracing, routing, and fetching cannot execute or silently transform payloads. | PR5 | specified - not runtime-verified |
| F22 | Existing D2a ignore-policy admission is preserved; object insertion cannot force ignored sources. | PR5 | specified - not runtime-verified |
| F23 | Contention, torn/stale controls, identity replacement, unsafe anchors, and anchor unlink/recreation refuse. | PR2 | specified - not runtime-verified |
| F24 | Participating CLI/library/nested/import/upgrade/recovery writers require capabilities and preserve lock order. | PR2 | specified - not runtime-verified |
| F25 | Object/index/publication/fsync failures yield observed or uncertain outcomes. | PR5/6 | specified - not runtime-verified |
| F26 | Missing checks, source/view disagreement, inventory drift, or malformed provenance prevents success. | PR3/6/7 | PR3b source/view disagreement preventing VIEWS-READY runtime-verified in draft (V1 correspondence, V2 changed source, V3 changed generator, V4 changed render, R1 C-VIEW-DRIFT); the success-gating legs PR6/7 pending |
| F27 | Lease/control release failures preserve the primary error and prevent exit 0. | PR2/6/7 | specified - not runtime-verified |
| F28 | Crash/retry at each registered boundary cannot overwrite, change decisions/HEAD, duplicate allocation, or re-adopt outside the ratified committed-deletion path; a retry resumes its original operation and plan. Reconciled with the PR3 ratification (PD-D2B-PR3-SCHEMA 2, 6). | PR3a/3b/6 | PR3a SOURCES-READY boundaries runtime-verified in draft (R4, R8, R9); PR3b VIEWS-READY boundaries runtime-verified in draft (R4 view points); PR6 pending |
| F29 | Reports distinguish attempts, observations, unknowns, ownership, staging, and mutation effects. | PR1/6 | specified - not runtime-verified |
| F30 | Preserve D2a distinction, unstaged views, import acceptance guarantees, rejected fragments, and existing-store history. | PR1-7 | PR3b unstaged views runtime-verified in draft (R1 views-unstaged, V5 staged-view refusal); the other legs specified - not runtime-verified |

Base revision (authored against): bd815bd

Each row's status is "specified - not runtime-verified" unless it names the self-test that runtime-verifies it: this register SPECIFIES the PR1 through PR7 refusal invariants and is not a runtime review of the implementation. A row's reviewed-revision binding is recorded when that invariant is runtime-verified in its owning PR; a "runtime-verified in draft" status carries no reviewed-revision binding until that PR merges.
