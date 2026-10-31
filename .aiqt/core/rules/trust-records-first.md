---
corpus-id: recfst
origin: pack
family: aiqt
tier: 10
facet: TRUST
secondary: [INTEG]
slug: records-first
map-nist-airmf-broad: [GOVERN 1.4]
map-nist-ssdf-tight: [PW.1.2]
map-iso-42001-broad: [7.5.2, 7.5.3]
map-iso-23894-tight: [6.7]
---

# Records first

Every ruling and decision is recorded to the durable store the session it is made. The record, not the
conversation, is the source of truth: a decision that is not recorded did not happen.

A brief or record stating review or fix rounds used or allowed cites durable round records for usage and
the recorded governing rule or authorized decision for the limit; where conflicting maintainer rulings
are each recorded in an artefact the maintainer controls and the assistant cannot write, the later
recorded one governs, as the decision-classification rule (deccls) sets out, and a missing basis, rulings
of unclear order and every other conflict in the basis, such as between round records or between a
governing rule and an authorized decision or another rule, are reported as unresolved rather than
substituted from memory.

Pending records stay durable without obstructing synchronization of the checkout tracking the protected
line; versioned records enter through reviewed integration.
