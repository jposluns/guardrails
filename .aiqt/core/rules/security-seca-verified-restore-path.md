---
corpus-id: secrst
origin: pack
family: security
facet: SECA
secondary: [INTEG]
slug: verified-restore-path
map-nist-80053-tight: [CP-9, CP-9(1), CP-10]
map-csa-ccm-tight: [BCR-08]
map-csa-aicm-tight: [BCR-08]
---

# A destructive operation requires a verified restore path

An operation that destroys or overwrites state proceeds only when the state it will destroy is
restorable through a backup, snapshot, or versioned copy whose restorability has been confirmed
against the actual target, or when the maintainer has explicitly accepted the loss. An untested
rollback idea is not evidence of reversibility: the restore path is verified before the destruction,
not designed after it. Authorization to destroy does not substitute for recoverability, and both may
be required at once.
