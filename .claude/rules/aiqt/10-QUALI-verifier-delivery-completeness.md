---
corpus-id: vrfdlv
origin: pack
family: aiqt
tier: 10
facet: QUALI
secondary: [ACCUR, INTEG]
slug: verifier-delivery-completeness
---

# A degraded verifier delivery is not a verdict

A verifier's output counts as a verdict only when the verifier delivered one. Completeness has one
criterion: the delivery carries positive evidence of a finished verdict that the verification agreed on
in advance, whether a completion marker the verifier emits, the explicit verdict the verification asked
for, or both. A delivery lacking that evidence, or truncated, empty, or errored, counts as no verdict,
never a pass, a vote, or a finding-free result.
A capture piped through a truncating sink is a degraded delivery, not a verdict, even when its retained
prefix appears complete.
Completeness is never inferred from output length or volume or from the absence of a reported problem,
because a run cut off before its verdict is indistinguishable from a clean one when judged by silence or
size. A delivery failing this test is re-dispatched and contributes nothing; it alone does not justify
reducing the verifier panel, since one failed delivery does not show a verifier family is unavailable. A
required family is dropped from the panel only when it is genuinely unreachable, on the terms the
verifier-diversity rule sets, and that reduction is recorded and re-run when the family returns.
The same fail-closed posture holds when the verifier cannot read or access a required input it should
cover: it emits no permissive verdict over the gap but UNVERIFIABLE, the cannot-evaluate outcome
guard-input-soundness names, identifying the unread input; this is a terminal fail-closed result, not the
transient degraded delivery above that a re-dispatch can cure, so it counts as not-passed, resolved by
fixing the input, never a clean, passing, or finding-free verdict. A clean verdict over an input the
verifier never read is the same false-clean this rule guards against, meeting the posture
check-fails-closed-on-unreadable requires of a gate that cannot read its own input.
A worker run ending in a safety refusal or provider content stop is a refusal, not a delivery, whatever
status line comes with it; the item is parked and reported to the maintainer, its request never
reworded, narrowed, split or reframed to get past the refusal.
