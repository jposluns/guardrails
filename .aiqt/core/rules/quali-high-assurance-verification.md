---
corpus-id: hiasrv
origin: pack
family: aiqt
tier: 10
facet: QUALI
secondary: [ACCUR]
slug: high-assurance-verification
map-nist-airmf-tight: [MEASURE 1.3]
map-nist-80053-broad: [CM-3(2), SA-11(3)]
map-nist-ssdf-broad: [PW.7.2]
map-iso-42001-broad: [A.6.2.4]
---

# High-assurance verification

Every substantive change is verified before it integrates by an independent adversarial pass, briefed to
refute rather than confirm.

A substantive change to a document that a gate or tool relies on as input, by quoting or matching its text
or parsing its structure (a mere mention of the document is not such reliance), is reviewed together with
each such gate or tool: the review brief names each one, found by searching the tooling for the document's
path and for the text the change alters, and states what that search could miss; a review of the document
alone is incomplete. The evidence-grounded-completion rule (evgcmp) governs when such a change is complete.
