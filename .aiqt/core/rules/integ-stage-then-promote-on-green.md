---
corpus-id: stgprm
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: stage-then-promote-on-green
---

# Stage artefacts and promote only on green

An artefact is never installed, published, or promoted to a live or otherwise consumer-accessible
destination before the verification it must pass has passed. It is placed first in a staging area that is
not that destination, verified there, and only then is that same verified artefact promoted to the live
destination, on a recorded pass. Promotion that races ahead of verification, or that ships a different
artefact than the one verified, is the failure this prevents. This governs an artefact reaching a runtime
or distribution target, and is distinct from integrating a change into the protected line of development.
The same order extends to a multi-step change to a live system: a tool's expected side effects come from
observing it first on a disposable copy or one canary step with a rehearsed undo, and the first harmless
surprise revises them all, not just the tripped item, while other checks stay as strict.
