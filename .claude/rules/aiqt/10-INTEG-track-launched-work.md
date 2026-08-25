---
corpus-id: trkasy
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: track-launched-work
map-nist-airmf-broad: [MANAGE 4.3]
map-iso-42001-broad: [A.6.2.6]
map-iso-23894-broad: [6.7]
---

# A launched task stays observable

A unit of work whose result or completion the caller depends on is launched so its outcome stays observable,
never discarded at the moment it starts. A task started and abandoned, a background process whose exit status
is never collected, an asynchronous result never awaited, or a queued job whose outcome no one reads, hides
any failure it carries and leaves a later claim that it finished ungrounded. Such work is launched through a
mechanism that tracks it to completion and surfaces both its result and its failures; a launch that drops the
completion signal is not a safe way to start work the caller relies on. Where a result is genuinely not
needed, running the task without tracking it is a deliberate, recorded choice, not the default.
