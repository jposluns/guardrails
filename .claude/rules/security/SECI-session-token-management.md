---
corpus-id: sectok
origin: pack
family: security
facet: SECI
secondary: [SECC]
slug: session-token-management
map-owasp-asvs: [V7, V9]
map-owasp-cheatsheet: [session-management]
---

# Secure session and token handling

Code the assistant writes and operations it performs handle sessions and tokens securely. Tokens carry
sufficient entropy, are transmitted and stored safely, are scoped and time-limited, and are invalidated on
logout, rotation, or suspected compromise.
