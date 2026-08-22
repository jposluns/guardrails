---
corpus-id: atmclm
origin: pack
family: aiqt
tier: 10
facet: TRUST
secondary: [INTEG]
slug: atomic-claim-from-pool
---

# Claim a pooled item atomically under one lock

Selecting an item from a shared pool and recording the claim on it are one atomic step, under a single
lock that spans both, so no gap between choosing and reserving lets two actors take the same item. A claim
already held by another live actor is not overridden: the operation aborts rather than proceeding or
merely warning. This extends the concurrency-lease discipline from a single session's lease to selection
from a shared pool.
