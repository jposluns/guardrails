---
corpus-id: valgat
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: validation-gates-apply
map-nist-airmf: [MAP 4.2]
map-nist-80053: [CM-3(2), SA-11]
map-nist-ssdf: [PW.7.1, PW.8.1]
map-iso-42001: [A.6.2.4]
---

# Validation is a gate on apply

There is no trusted-worker fast path: every candidate change is validated before it lands, no matter its
source. Trust is never a substitute for the gate.
