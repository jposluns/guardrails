---
corpus-id: valgat
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: validation-gates-apply
map-nist-airmf-broad: [MAP 4.2]
map-nist-80053-tight: [CM-3(2)]
map-nist-80053-broad: [SA-11]
map-nist-ssdf-broad: [PW.7.1, PW.8.1]
map-iso-42001-broad: [A.6.2.4]
---

# Validation is a gate on apply

There is no trusted-worker fast path: every candidate change is validated before it lands, no matter its
source. Trust is never a substitute for the gate.

Before a worker-proposed field change is applied, its proposed value is compared with the field's prior
values on the branch; restoration after an intervening different value awaits one adjudication, with the
ruling durably registered, carried in later triage briefs and consulted before reopening the same
conflict.
