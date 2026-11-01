---
corpus-id: sesres
origin: pack
family: aiqt
tier: 10
facet: TRUST
secondary: [PROGR]
slug: session-resume-from-handoff
map-iso-42001-broad: [8.1]
---

# Resume from the durable handoff

A session resumes from its durable handoff rather than starting cold, and works under a single named
operating mode. Where the project keeps a memory store, the session reads the store's index, if it has
one, before it dispatches work, and when its memory path is missing or does not resolve to that store, it
holds and names the path rather than working without the store.
