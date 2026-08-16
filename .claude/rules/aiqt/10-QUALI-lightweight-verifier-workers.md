---
corpus-id: lvw001
origin: pack
family: aiqt
tier: 10
facet: QUALI
slug: lightweight-verifier-workers
---

# Lightweight cross-family verifiers

A cross-family skeptical verifier does not need a heavy multi-tenant apparatus: run each family read-only in its own scoped config directory, dispatching a prompt file to it. Classify the outcome on the process
EXIT CODE, never by grepping the output, because a verifier echoes the very rule text under review
(discussing usage, rate limits, and re-auth), so grepping successful output for those words false-positives.
