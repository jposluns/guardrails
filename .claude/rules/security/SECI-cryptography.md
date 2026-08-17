---
corpus-id: seccry
origin: pack
family: security
facet: SECI
secondary: [SECC]
slug: cryptography
map-nist-80053: [SC-8, SC-13, SC-28]
map-nist-ssdf: [PW.5.1]
map-iso-23894: [A.8, A.11]
map-owasp-web: [A04]
map-owasp-asvs: [V11, V12]
map-owasp-proactive: [C2]
map-owasp-cheatsheet: [transport-layer-security]
---

# Sound cryptography

Cryptography uses current, approved algorithms and correct parameters, with no weak, deprecated, or
home-grown schemes. Data is protected in transit and at rest to the strength its sensitivity requires.
Protection in transit is never defeated by disabling its verification: certificate and hostname validation
stay on, and code never disables TLS peer verification or accepts a self-signed or mismatched certificate
to work around a connection error.
