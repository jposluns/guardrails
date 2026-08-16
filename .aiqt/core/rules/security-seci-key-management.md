---
corpus-id: seckey
origin: pack
family: security
facet: SECI
secondary: [SECC]
slug: key-management
map-nist-80053: [IA-5(7), SC-12]
map-nist-ssdf: [PW.5.1]
map-owasp-asvs: [V11]
map-owasp-cheatsheet: [key-management]
---

# Key management

Keys and other secret material are generated, stored, rotated, and retired properly, and are never hardcoded.
A key is never reused across contexts that are meant to stay isolated from one another.
