---
corpus-id: fndfix
origin: pack
family: aiqt
tier: 10
facet: QUALI
secondary: [INTEG, TRUST]
slug: findings-are-fixed-not-argued
map-nist-airmf-broad: [GOVERN 4.1, MANAGE 2.3]
map-nist-80053-tight: [SI-2]
map-nist-80053-broad: [SA-11]
map-nist-ssdf-broad: [PO.4.1, PW.7.2]
map-iso-42001-broad: [A.6.2.4]
---

# A verification finding is fixed, not argued away

A finding raised by an adversarial verification pass is fixed, not argued away. A real blocker or major
finding from any independent verifier blocks the change, fail-closed, until it is fixed or the maintainer
explicitly reclassifies it with a recorded rationale. A blocker or major finding closes only by a fix or
the maintainer's recorded reclassification: a refutation, an argument against the finding, is evidence
offered to the maintainer for that reclassification, never itself a closure, whoever wrote or accepted it;
it names each cause it ruled out with the reading or observation that ruled it out, and the finding stays
open for every other cause of the named condition.
