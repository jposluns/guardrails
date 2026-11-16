---
corpus-id: gensrc
origin: pack
family: aiqt
tier: 10
facet: INTEG
slug: generated-artefact-source-only
---

# A generated artefact is changed only through its source

A derived or generated artefact is never hand-edited; it changes only by changing its source and
regenerating. Source and derivative land in the same change, so the two cannot drift apart.
Deriving an artefact leaves its source originals byte-for-byte unchanged and writes separate outputs;
in-place derivation uses copies in an area excluding the originals and refuses any path or alias
writing outside it or back to an original.

When case-dependent behaviour is described in more than one maintained place, keep its cases and
qualifications in one canonical source; every other maintained description references that source
or is generated from it, and a summary that points to the source restates no case. Where those cases
are machine-checkable, use them as test inputs and expected outcomes against the implementation.
A record of a past state, such as a commit message, a changelog entry or a review report, is not a
maintained description.
