---
corpus-id: sectok
origin: pack
family: security
facet: SECI
secondary: [SECC]
slug: session-token-management
map-nist-80053: [IA-5, SC-23, SC-23(1)]
map-nist-ssdf: [PW.5.1]
map-atlas: [AML.T0055, AML.T0091.000, AML.T0091.001, AML.T0113]
map-owasp-asvs: [V7, V9]
map-owasp-cheatsheet: [session-management]
---

# Secure session and token handling

Code the assistant writes and operations it performs handle sessions and tokens securely. Tokens carry
sufficient entropy, are transmitted and stored safely, are scoped and time-limited, and are invalidated on
logout, rotation, or suspected compromise.
