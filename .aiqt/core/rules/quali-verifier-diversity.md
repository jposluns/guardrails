---
corpus-id: vrfdiv
origin: pack
family: aiqt
tier: 10
facet: QUALI
slug: verifier-diversity
---

# Verifier diversity

Diversify the verifiers so they surface different failure classes: run the adversarial pass across two
model families, and a second family from any vendor counts. Only where no second model family is available
may this fall back to two independent, differently-primed passes in separate clean contexts, which is the
accepted fallback and not the equal of two families; record the reduction and run the two-family pass once
a second family becomes available. A third family is reserved for critical changes; where only one vendor
is reachable, a further independent pass takes its place.
