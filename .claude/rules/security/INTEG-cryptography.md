---
corpus-id: seccry
origin: pack
family: security
facet: INTEG
slug: cryptography
map-owasp-web: [A04]
map-owasp-asvs: [V11, V12]
map-owasp-proactive: [C2]
---

# Sound cryptography and key handling

Cryptography uses current, approved algorithms and correct parameters, with no weak, deprecated, or
home-grown schemes. Keys and other secret material are generated, stored, rotated, and retired properly,
never hardcoded and never reused across contexts that should stay isolated. Data is protected in transit
and at rest to the strength its sensitivity requires.
