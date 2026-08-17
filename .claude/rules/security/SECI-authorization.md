---
corpus-id: secazn
origin: pack
family: security
facet: SECI
secondary: [SECC]
slug: authorization
map-nist-80053: [AC-3, AC-6]
map-nist-ssdf: [PW.5.1]
map-atlas: [AML.T0053, AML.T0082, AML.T0085]
map-iso-23894: [A.11]
map-owasp-asvs: [V8]
map-owasp-web: [A01]
map-owasp-api: [API1, API3, API5]
map-owasp-proactive: [C1]
map-owasp-cheatsheet: [authorization, mass-assignment]
---

# Least-privilege authorization

Code the assistant writes and operations it performs enforce least-privilege authorization, checked at every
access rather than inferred from an earlier step. Verification happens at the object, function, and property
level on each request: a write binds only to an explicit allow-list of fields, so a caller can never reach
data or actions, nor set a protected field through mass assignment, beyond what its own rights permit.
