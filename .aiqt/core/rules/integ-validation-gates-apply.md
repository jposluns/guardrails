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
source. Trust is never a substitute for the gate. Before a dispatch to a callee whose own interface or
documentation states conditions under which it refuses an input (environmental failures such as a
timeout, a rate limit, an authorization denial, or a model declining are not such conditions), the
caller evaluates each stated condition it can settle cheaply and with certainty from inputs it already
has or can read without side effects, withholds the dispatch when such a condition is met and reports in
its own result a refusal naming that condition, and leaves every other condition to the callee's own
check, which it retains; for a pre-check ahead of an expensive apply, the guard-input-soundness rule
(grdinp) sets the bar instead.
