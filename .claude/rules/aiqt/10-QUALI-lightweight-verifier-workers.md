---
corpus-id: lvw001
origin: pack
family: aiqt
tier: 10
facet: QUALI
secondary: [ACCUR, SECI]
slug: lightweight-verifier-workers
---

# Isolate verifiers and judge by their result signal

Run each verifier in its own isolated, read-only context. Judge its outcome by an authoritative result
signal, not by grepping its output: a verifier legitimately echoes the very rule text under review, so
text-matching its output produces false positives.
