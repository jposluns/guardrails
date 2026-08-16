---
corpus-id: secsec
origin: pack
family: security
facet: CONFI
slug: secrets
---

# Secrets

No credential, token, key, or other secret is committed to a repository or written to a shared location.
A secret that reaches a remote is treated as COMPROMISED and rotated, whatever any scanner said. Pattern
scanning and a leak gate are compensating controls, never a substitute for keeping secrets out. Security
is the emergent result of doing AIQT well, so it is filed by its own model, not as a priority tier.
