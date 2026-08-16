---
corpus-id: seslif
origin: pack
family: aiqt
tier: 10
facet: TRUST
slug: session-lifecycle
---

# Session lifecycle

Sessions RESUME from a durable handoff, WORK under a named operating mode, and CLOSE by landing working
state cleanly on the protected branch. A concurrency lease prevents a double-run and is
reconciled, never stolen. The default at every point is to continue; a wind-down needs a named,
externally-observable trigger, never a felt sense of degradation.
