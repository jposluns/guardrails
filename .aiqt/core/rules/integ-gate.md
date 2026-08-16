---
corpus-id: gatdis
origin: pack
family: aiqt
tier: 10
facet: INTEG
slug: gate-discipline
---

# Gate discipline

Never weaken a gate to obtain a pass. Fix the artefact instead. No bypass flags, no piping a check to a
truncating sink, no `|| true`, no deleted tests, no lowered thresholds. A failing gate is signal;
understand it before overriding. No stubbed, mocked, or simulated result is presented as finished; a
failing state is surfaced, never concealed.
