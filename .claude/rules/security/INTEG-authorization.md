---
corpus-id: secazn
origin: pack
family: security
facet: INTEG
slug: authorization
map-owasp-asvs: [V8]
map-owasp-web: [A01]
map-owasp-api: [API1, API5]
map-owasp-proactive: [C1]
---

# Least-privilege authorization

Code the assistant writes and operations it performs enforce least-privilege authorization, checked at every
access rather than inferred from an earlier step. Verification happens at both the object and function level
on each request, so a caller can never reach data or actions beyond what its own rights permit.
