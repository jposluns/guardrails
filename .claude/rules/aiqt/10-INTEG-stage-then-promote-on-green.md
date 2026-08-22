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

An artefact is never installed, published, or copied to a live or shared location before the verification
it must pass has passed. It is placed first in a staging location, verified there, and only then is that
same verified artefact promoted to the live or shared destination, on a recorded pass. Promotion that
races ahead of verification, or that ships a different artefact than the one verified, is the failure this
prevents. This governs an artefact reaching a runtime or distribution target, and is distinct from
integrating a change into the protected line of development.
