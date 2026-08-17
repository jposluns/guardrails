---
corpus-id: secfcl
origin: pack
family: security
facet: SECI
slug: fail-closed
map-owasp-web: [A10]
map-owasp-cheatsheet: [error-handling]
---

# Fail closed in security-relevant paths

An exception or error in an authentication, authorization, validation, or cryptographic check leaves the
system in the deny or otherwise safe state. The error is surfaced for handling and investigation, never
swallowed into a default-allow path or treated as if the check had passed.
