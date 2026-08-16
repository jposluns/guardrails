---
corpus-id: bgcwai
origin: pack
family: aiqt
tier: 30
facet: SPEED
secondary: [INTEG, PROGR]
slug: background-work-during-ci-waits
---

# Background work during CI waits

A wait is a resource. While CI or another long operation runs, advance independent, non-conflicting work
rather than idling, without ever gating the outcome on an unread or pending result. Never merge on a
pending or unreadable signal; parallelism speeds the work, it never lowers the bar.
