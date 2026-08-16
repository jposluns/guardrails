---
corpus-id: seckey
origin: pack
family: security
facet: SECI
slug: key-management
map-owasp-asvs: [V11]
map-owasp-cheatsheet: [key-management]
---

# Key management

Keys and other secret material are generated, stored, rotated, and retired properly, and are never hardcoded.
A key is never reused across contexts that are meant to stay isolated from one another.
