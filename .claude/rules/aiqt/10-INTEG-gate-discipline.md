---
corpus-id: gatdis
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: gate-discipline
map-nist-airmf: [GOVERN 4.1]
map-nist-80053: [CM-3(2), SA-11]
map-nist-ssdf: [PO.4.1, PW.8.2]
map-iso-42001: [A.6.2.4]
---

# Gate discipline

Never weaken a gate to obtain a pass; fix the artefact instead. No bypass flags, no piping a check to a
truncating sink, no `|| true`, no deleted tests, no lowered thresholds. A failing gate is signal;
understand why it failed before considering any override.
