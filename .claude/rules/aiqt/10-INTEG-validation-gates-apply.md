---
corpus-id: valgat
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: validation-gates-apply
map-nist-airmf-broad: [MAP 4.2]
map-nist-80053-broad: [CM-3(2), SA-11]
map-nist-ssdf-broad: [PW.7.1, PW.8.1]
map-iso-42001-broad: [A.6.2.4]
---

# Validation is a gate on apply

There is no trusted-worker fast path: every candidate change is validated before it lands, no matter its
source. Trust is never a substitute for the gate. Before a dispatch to a callee whose interface or
documentation states conditions under which it refuses an input, other than a failure of its environment
(a condition that does not depend on the content of the input, such as a timeout, a rate limit, or an
outage), the caller evaluates each stated condition whose inputs it already holds or can obtain by a read
that changes no state, withholds the dispatch when such a condition is met, and reports a refusal naming
that condition in its own result; it leaves to the callee's retained check only a condition whose inputs
it cannot so obtain. Where the dispatch starts an expensive apply, that evaluation is also a check ahead
of the apply and meets guard-input-soundness (grdinp) as well.
