---
corpus-id: wowo01
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI, SECI]
slug: workers-produce-inert-data
map-nist-airmf-broad: [MAP 4.2]
map-nist-80053-tight: [AC-6, CM-5]
map-nist-ssdf-tight: [PS.1.1]
map-nist-ssdf-broad: [PW.7.2]
map-atlas-broad: [AML.T0053]
map-iso-42001-broad: [A.6.1.3]
map-iso-23894-broad: [A.2, B.4]
---

# Workers produce inert data

Workers produce research and candidate diffs as inert data; one orchestrator re-reads, verifies, and
integrates them, and workers apply nothing themselves. Parallelism lives in the research stage; authority
and seriality live in the apply stage.

Narrow recovery-snapshot exception: a guard or hook that snapshots uncommitted work so a discard can be
undone may write a private recovery ref under `refs/aiqt-recovery/` (and the objects it points to). This
changes no working state (it is not a branch, HEAD, the index, or the working tree, and is invisible to
status, branch, and log), so it keeps the inert posture. The exception is limited to recovery snapshots
written for the actor's own protection; it is not a general licence to mutate repository metadata.
