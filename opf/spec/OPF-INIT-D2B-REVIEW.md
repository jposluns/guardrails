# OPF-INIT-D2B-REVIEW

Durable sensitive-tier review register for OPF initialization.

| ID | Required invariant and evidence | Owner | Status |
|---|---|---|---|
| F01 | Wrong root/prefix/repository/worktree/common-dir/index cannot bind; nested and linked-worktree fixtures. | PR3/5 | specified - not runtime-verified |
| F02 | Nonrepo, bare, unsupported topology/platform/filesystem/index format, or unconfirmed target refuses. | PR2/3/5 | specified - not runtime-verified |
| F03 | Both pointers, alternate stores, retired tokens, malformed/ambiguous/partial adoption refuse. | PR3 | specified - not runtime-verified |
| F04 | Adoption deleted at HEAD but present in reachable ancestry refuses fresh init. | PR3 | specified - not runtime-verified |
| F05 | Shallow/missing/unreadable/malformed/bounded-out history is cannot-evaluate; no lazy fetch. | PR3 | specified - not runtime-verified |
| F06 | Replacement refs, grafts, commit-graph effects, changed HEAD/binding, and broken refs cannot substitute observations. | PR3 | specified - not runtime-verified |
| F07 | Failed resolution is not unborn proof; valid symbolic HEAD and absent target are independently established. | PR3 | specified - not runtime-verified |
| F08 | Forged/stale provenance, missing publication evidence, or committed damage cannot authorize empty prior. | PR3/6 | specified - not runtime-verified |
| F09 | Hidden entries, empty directories, read errors, disappearing entries, and exhausted caps cannot disappear from inventory. | PR4 | specified - not runtime-verified |
| F10 | Links, special files, unsafe hardlinks, embedded repos/gitlinks, and replaced ancestors refuse unsafe access. | PR3-6 | specified - not runtime-verified |
| F11 | Closed decisions reject malformed fields, caps, duplicates, overlaps, ghosts, unused choices, and missing coverage. | PR1/4 | specified - not runtime-verified |
| F12 | Root/HEAD/content/mode/type/group changes invalidate acceptance before adoption. | PR1/4/6 | specified - not runtime-verified |
| F13 | Plans, proposals, report edits, and blanket consent cannot substitute for explicit attributed acceptance. | PR4/7 | specified - not runtime-verified |
| F14 | Managed/reserved destination collisions refuse even with identical bytes or generated headers. | PR1/4/6 | specified - not runtime-verified |
| F15 | After unmanaged adoption, no retained-body reads, staging, or widened exemptions. | PR4/6/7 | specified - not runtime-verified |
| F16 | Keep worklog entries are deterministic, consecutive, attributed infra; counters agree; no repeated allocation or invented partial import. | PR4/6 | specified - not runtime-verified |
| F17 | Incompatible changelog and VERSION in worktree/index/HEAD refuse D2b admission. | PR4 | specified - not runtime-verified |
| F18 | Publication races and partial/same-byte foreign files cannot bypass exclusive creation and ownership evidence. | PR6 | specified - not runtime-verified |
| F19 | Only exact S file entries enter the index; no expansion, conflict stages, intent-to-add, or V/K/E/C. | PR5 | specified - not runtime-verified |
| F20 | Unrelated index entries/flags/metadata survive; unsupported extensions/formats and corrupt indexes refuse. | PR5 | specified - not runtime-verified |
| F21 | Git configuration, filters, encodings, helpers, hooks, fsmonitor, tracing, routing, and fetching cannot execute or silently transform payloads. | PR5 | specified - not runtime-verified |
| F22 | Existing D2a ignore-policy admission is preserved; object insertion cannot force ignored sources. | PR5 | specified - not runtime-verified |
| F23 | Contention, torn/stale controls, identity replacement, unsafe anchors, and anchor unlink/recreation refuse. | PR2 | specified - not runtime-verified |
| F24 | Participating CLI/library/nested/import/upgrade/recovery writers require capabilities and preserve lock order. | PR2 | specified - not runtime-verified |
| F25 | Object/index/publication/fsync failures yield observed or uncertain outcomes. | PR5/6 | specified - not runtime-verified |
| F26 | Missing checks, source/view disagreement, inventory drift, or malformed provenance prevents success. | PR3/6/7 | specified - not runtime-verified |
| F27 | Lease/control release failures preserve the primary error and prevent exit 0. | PR2/6/7 | specified - not runtime-verified |
| F28 | Crash/retry at each registered boundary cannot overwrite, re-adopt, change decisions/HEAD, or duplicate allocation. | PR6 | specified - not runtime-verified |
| F29 | Reports distinguish attempts, observations, unknowns, ownership, staging, and mutation effects. | PR1/6 | specified - not runtime-verified |
| F30 | Preserve D2a distinction, unstaged views, import acceptance guarantees, rejected fragments, and existing-store history. | PR1-7 | specified - not runtime-verified |

Reviewed revision: bd815bd
