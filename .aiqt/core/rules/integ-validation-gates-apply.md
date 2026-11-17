---
corpus-id: valgat
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: validation-gates-apply
map-nist-airmf-broad: [MAP 4.2]
map-nist-80053-broad: [CM-3(2), SA-11]
map-nist-ssdf-broad: [PW.7.1, PW.8.1]
map-iso-42001-broad: [A.6.2.4]
---

# Validation is a gate on apply

There is no trusted-worker fast path: every candidate change is validated before it lands, no matter its
source. Trust is never a substitute for the gate.
A path that writes a record a later gate reads validates it with that gate's definition, or, where the
definition is out of reach, a test holds the writer to refusing what the gate would refuse.
