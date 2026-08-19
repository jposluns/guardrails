---
corpus-id: chgchk
origin: pack
family: aiqt
tier: 10
facet: QUALI
secondary: [INTEG]
slug: change-carries-check
map-nist-80053-broad: [CM-3(2), SA-11]
map-nist-ssdf-broad: [PW.8.2]
map-iso-42001-broad: [A.6.2.4]
---

# A behavioural change carries a check that fails without it

A change that alters behaviour lands together with an automated test or gate that fails when the change is
absent. Verification leaves a durable artefact that keeps guarding the behaviour after the one-time
verification pass has moved on.
