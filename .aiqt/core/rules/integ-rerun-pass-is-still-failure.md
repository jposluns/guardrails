---
corpus-id: rerunf
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: rerun-pass-is-still-failure
map-nist-80053-broad: [SI-2]
map-iso-42001-broad: [A.6.2.4]
---

# A pass obtained by rerunning is still the failure

A check that fails and then passes on rerun with no intervening change is treated as a still-open
intermittent defect: the earlier failure is recorded and investigated, and the rerun's green is not
presented as the verification the original run was meant to give. Retrying until the gate agrees
changes nothing the gate measures, so the failure keeps its signal even though the final state reads
green.
